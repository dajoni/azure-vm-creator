#!/usr/bin/env python3
"""Create a Windows VM reachable through Azure Bastion Developer.

The VM is created with a public IP address for outbound internet access, but no
public inbound RDP rule. Azure CLI's default subscription from `az account show`
is used as the deployment target.
"""

from __future__ import annotations

import argparse
import getpass
import json
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


Json = dict[str, Any] | list[Any] | str | int | float | bool | None

DEFAULT_LOCATION = "northeurope"
DEFAULT_RESOURCE_GROUP = "rg-secure-winvm"
DEFAULT_VM_NAME = "vm-secure-win"
DEFAULT_ADMIN_USERNAME = "azureuser"
DEFAULT_VM_SIZE = "Standard_B2as_v2"
DEFAULT_IMAGE_PUBLISHER = "MicrosoftWindowsServer"
DEFAULT_IMAGE_OFFER = "WindowsServer"
DEFAULT_IMAGE_SKU = "2025-datacenter-azure-edition"
DEFAULT_IMAGE_VERSION = "latest"
DEFAULT_IMAGE = f"{DEFAULT_IMAGE_PUBLISHER}:{DEFAULT_IMAGE_OFFER}:{DEFAULT_IMAGE_SKU}:{DEFAULT_IMAGE_VERSION}"
DEFAULT_STORAGE_SKU = "StandardSSD_LRS"
DEFAULT_OS_DISK_SIZE_GB = 127
DEFAULT_OS_DISK_DELETE_OPTION = "Delete"
DEFAULT_VNET_NAME = "vnet-secure-win"
DEFAULT_SUBNET_NAME = "subnet-secure-win"
DEFAULT_BASTION_NAME = "bastion-secure-win"
DEFAULT_VM_PUBLIC_IP_NAME = "pip-secure-win"
DEFAULT_CONFIG_SCRIPT = "scripts/configure_windows_vm.ps1"
DEFAULT_RUN_COMMAND_NAME = "configure-windows-vm"
DEFAULT_VNET_PREFIX = "10.42.0.0/16"
DEFAULT_SUBNET_PREFIX = "10.42.1.0/24"


@dataclass
class CommandResult:
    ok: bool
    command: list[str]
    data: Json = None
    error: str = ""


@dataclass
class DeploymentState:
    subscription_id: str
    subscription_name: str
    tenant_id: str
    rg: CommandResult
    vnet: CommandResult
    subnet: CommandResult
    bastion: CommandResult
    public_ip_resource: CommandResult
    vm: CommandResult


@dataclass
class ResourcePlan:
    needs_group: bool
    needs_vnet: bool
    needs_subnet: bool
    needs_bastion: bool
    needs_vm_public_ip: bool
    needs_vm: bool

    @property
    def needs_anything(self) -> bool:
        return (
            self.needs_group
            or self.needs_vnet
            or self.needs_subnet
            or self.needs_bastion
            or self.needs_vm_public_ip
            or self.needs_vm
        )


SENSITIVE_FLAGS = {"--admin-password", "--password", "--run-as-password", "--secret", "--value"}


def redacted_command(command: list[str]) -> str:
    redacted: list[str] = []
    redact_next = False
    for part in command:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        redacted.append(part)
        if part in SENSITIVE_FLAGS:
            redact_next = True
    return " ".join(shlex.quote(part) for part in redacted)


def az(args: list[str], timeout: int = 300) -> CommandResult:
    command = ["az", *args, "--only-show-errors", "--output", "json"]
    print(f"$ {redacted_command(command)}", file=sys.stderr)
    try:
        proc = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return CommandResult(False, command, error="Azure CLI executable `az` was not found.")
    except subprocess.TimeoutExpired as exc:
        return CommandResult(False, command, error=f"Timed out after {exc.timeout} seconds.")

    if proc.returncode != 0:
        return CommandResult(False, command, error=(proc.stderr or proc.stdout).strip())

    stdout = proc.stdout.strip()
    if not stdout:
        return CommandResult(True, command, data=None)

    try:
        return CommandResult(True, command, data=json.loads(stdout))
    except json.JSONDecodeError:
        return CommandResult(False, command, error=f"Command returned non-JSON output: {stdout[:500]}")


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("value"), list):
        return value["value"]
    return [value]


def concise_error(error: Any) -> str:
    lines = [line.strip() for line in str(error or "").splitlines() if line.strip()]
    for line in lines:
        if line.startswith("ERROR:"):
            return line
    return lines[0] if lines else "unavailable"


def fail(message: str, result: CommandResult | None = None) -> int:
    print(message, file=sys.stderr)
    if result and result.error:
        print(concise_error(result.error), file=sys.stderr)
    return 1


def get_path(value: Any, *paths: str, default: Any = None) -> Any:
    for path in paths:
        current = value
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                current = None
                break
        if current is not None:
            return current
    return default


def run_step(label: str, args: list[str], execute: bool, timeout: int = 300) -> CommandResult:
    if not execute:
        print(f"- would {label}")
        return CommandResult(True, ["az", *args])
    print(f"- {label}")
    result = az(args, timeout=timeout)
    if not result.ok:
        print(f"  failed: {concise_error(result.error)}")
    return result


def show_resource_group(name: str) -> CommandResult:
    return az(["group", "show", "--name", name], timeout=120)


def show_vnet(resource_group: str, name: str) -> CommandResult:
    return az(["network", "vnet", "show", "--resource-group", resource_group, "--name", name], timeout=120)


def show_subnet(resource_group: str, vnet_name: str, subnet_name: str) -> CommandResult:
    return az(
        [
            "network",
            "vnet",
            "subnet",
            "show",
            "--resource-group",
            resource_group,
            "--vnet-name",
            vnet_name,
            "--name",
            subnet_name,
        ],
        timeout=120,
    )


def show_bastion(resource_group: str, name: str) -> CommandResult:
    return az(["network", "bastion", "show", "--resource-group", resource_group, "--name", name], timeout=180)


def show_public_ip(resource_group: str, name: str) -> CommandResult:
    return az(["network", "public-ip", "show", "--resource-group", resource_group, "--name", name], timeout=120)


def show_vm(resource_group: str, name: str) -> CommandResult:
    return az(["vm", "show", "--resource-group", resource_group, "--name", name, "--show-details"], timeout=180)


def show_nic_by_id(nic_id: str) -> CommandResult:
    return az(["network", "nic", "show", "--ids", nic_id], timeout=120)


def show_nsg_by_id(nsg_id: str) -> CommandResult:
    return az(["network", "nsg", "show", "--ids", nsg_id], timeout=120)


def show_vm_run_command(resource_group: str, vm_name: str, run_command_name: str) -> CommandResult:
    return az(
        [
            "vm",
            "run-command",
            "show",
            "--resource-group",
            resource_group,
            "--vm-name",
            vm_name,
            "--run-command-name",
            run_command_name,
        ],
        timeout=120,
    )


def show_vm_run_command_instance_view(resource_group: str, vm_name: str, run_command_name: str) -> CommandResult:
    return az(
        [
            "vm",
            "run-command",
            "show",
            "--resource-group",
            resource_group,
            "--vm-name",
            vm_name,
            "--run-command-name",
            run_command_name,
            "--instance-view",
        ],
        timeout=120,
    )


def resource_missing(result: CommandResult) -> bool:
    error = result.error.lower()
    return (
        not result.ok
        and (
            "could not be found" in error
            or "was not found" in error
            or "resourcenotfound" in error
            or "notfound" in error
        )
    )


def ensure_no_unexpected_error(label: str, result: CommandResult) -> int:
    if result.ok or resource_missing(result):
        return 0
    return fail(f"Unable to inspect {label}.", result)


def validate_password(password: str) -> str | None:
    if len(password) < 12:
        return "Password must be at least 12 characters."
    if len(password) > 123:
        return "Password must be no more than 123 characters."
    classes = 0
    classes += any(ch.islower() for ch in password)
    classes += any(ch.isupper() for ch in password)
    classes += any(ch.isdigit() for ch in password)
    classes += any(not ch.isalnum() for ch in password)
    if classes < 3:
        return "Password must use at least three of: lowercase, uppercase, digits, symbols."
    return None


def prompt_password() -> str:
    while True:
        password = getpass.getpass("Windows admin password: ")
        confirm = getpass.getpass("Confirm Windows admin password: ")
        if password != confirm:
            print("Passwords did not match.", file=sys.stderr)
            continue
        error = validate_password(password)
        if error:
            print(error, file=sys.stderr)
            continue
        return password


def vm_private_ip(resource_group: str, vm_name: str) -> str:
    result = az(["vm", "list-ip-addresses", "--resource-group", resource_group, "--name", vm_name], timeout=120)
    if not result.ok:
        return "unavailable"
    entries = as_list(result.data)
    if not entries or not isinstance(entries[0], dict):
        return "unavailable"
    private_ips = get_path(entries[0], "virtualMachine.network.privateIpAddresses", default=[])
    if isinstance(private_ips, list) and private_ips:
        return str(private_ips[0])
    return "unavailable"


def vm_public_ip_address(resource_group: str, vm_name: str) -> str:
    result = az(["vm", "list-ip-addresses", "--resource-group", resource_group, "--name", vm_name], timeout=120)
    if not result.ok:
        return "unavailable"
    entries = as_list(result.data)
    if not entries or not isinstance(entries[0], dict):
        return "unavailable"
    public_ips = get_path(entries[0], "virtualMachine.network.publicIpAddresses", default=[])
    if isinstance(public_ips, list) and public_ips:
        first = public_ips[0]
        if isinstance(first, dict):
            return str(first.get("ipAddress") or first.get("name") or "unavailable")
        return str(first)
    return "unavailable"


def vm_has_public_ip(vm: dict[str, Any]) -> bool:
    public_ips = get_path(vm, "publicIps", default="")
    return bool(str(public_ips or "").strip())


def resource_id_name(resource_id: str) -> str:
    return resource_id.rstrip("/").split("/")[-1] if resource_id else ""


def resource_id_equals(left: str, right: str) -> bool:
    return left.rstrip("/").lower() == right.rstrip("/").lower()


def vm_nic_ids(vm: dict[str, Any]) -> list[str]:
    interfaces = get_path(vm, "networkProfile.networkInterfaces", default=[])
    return [
        str(interface.get("id") or "")
        for interface in as_list(interfaces)
        if isinstance(interface, dict) and interface.get("id")
    ]


def nic_public_ip_ids(nic: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for ip_config in as_list(nic.get("ipConfigurations")):
        if not isinstance(ip_config, dict):
            continue
        public_ip_id = str(get_path(ip_config, "publicIPAddress.id", default="") or "")
        if public_ip_id:
            ids.append(public_ip_id)
    return ids


def nic_subnet_ids(nic: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for ip_config in as_list(nic.get("ipConfigurations")):
        if not isinstance(ip_config, dict):
            continue
        subnet_id = str(get_path(ip_config, "subnet.id", default="") or "")
        if subnet_id:
            ids.append(subnet_id)
    return ids


def port_range_includes(port_range: str, port: int) -> bool:
    value = str(port_range or "").strip()
    if value == "*":
        return True
    if "-" in value:
        start, _, end = value.partition("-")
        try:
            return int(start) <= port <= int(end)
        except ValueError:
            return False
    try:
        return int(value) == port
    except ValueError:
        return False


def public_source_prefix(prefix: str) -> bool:
    normalized = str(prefix or "").strip().lower()
    return normalized in {"*", "internet", "any", "0.0.0.0/0", "::/0"}


def rule_allows_public_rdp(rule: dict[str, Any]) -> bool:
    if str(rule.get("access") or "").lower() != "allow":
        return False
    if str(rule.get("direction") or "").lower() != "inbound":
        return False
    protocol = str(rule.get("protocol") or "").lower()
    if protocol not in {"*", "tcp"}:
        return False

    sources = as_list(rule.get("sourceAddressPrefixes")) or [rule.get("sourceAddressPrefix")]
    if not any(public_source_prefix(str(source or "")) for source in sources):
        return False

    ports = as_list(rule.get("destinationPortRanges")) or [rule.get("destinationPortRange")]
    return any(port_range_includes(str(port or ""), 3389) for port in ports)


def compact_values(*values: Any) -> str:
    items: list[str] = []
    for value in values:
        for item in as_list(value):
            if item is None:
                continue
            text = str(item).strip()
            if text:
                items.append(text)
    return ",".join(items) if items else "-"


def format_nsg_rule(rule: dict[str, Any]) -> str:
    priority = str(rule.get("priority") or "-")
    name = str(rule.get("name") or "unnamed")
    direction = str(rule.get("direction") or "-")
    access = str(rule.get("access") or "-")
    protocol = str(rule.get("protocol") or "-")
    source = compact_values(rule.get("sourceAddressPrefixes"), rule.get("sourceAddressPrefix"))
    source_ports = compact_values(rule.get("sourcePortRanges"), rule.get("sourcePortRange"))
    destination = compact_values(rule.get("destinationAddressPrefixes"), rule.get("destinationAddressPrefix"))
    destination_ports = compact_values(rule.get("destinationPortRanges"), rule.get("destinationPortRange"))
    return (
        f"{priority} {name}: {direction} {access} {protocol} "
        f"src={source}:{source_ports} dst={destination}:{destination_ports}"
    )


def vm_os_disk_conflicts(vm: dict[str, Any], storage_sku: str, os_disk_size_gb: int, delete_option: str) -> list[str]:
    conflicts: list[str] = []
    actual_storage_sku = str(get_path(vm, "storageProfile.osDisk.managedDisk.storageAccountType", default="") or "")
    actual_size = get_path(vm, "storageProfile.osDisk.diskSizeGb")
    actual_delete_option = str(get_path(vm, "storageProfile.osDisk.deleteOption", default="") or "")

    if actual_storage_sku and actual_storage_sku.lower() != storage_sku.lower():
        conflicts.append(f"OS disk storage SKU is {actual_storage_sku}, not {storage_sku}")
    if actual_size is not None and int(actual_size) != os_disk_size_gb:
        conflicts.append(f"OS disk size is {actual_size} GB, not {os_disk_size_gb} GB")
    if actual_delete_option and actual_delete_option.lower() != delete_option.lower():
        conflicts.append(f"OS disk delete option is {actual_delete_option}, not {delete_option}")
    return conflicts


def vm_image_conflicts(vm: dict[str, Any], image_sku: str) -> list[str]:
    expected = vm_image(image_sku).split(":")
    conflicts: list[str] = []
    if len(expected) < 3:
        return conflicts
    expected_publisher, expected_offer, expected_sku = expected[:3]
    actual_publisher = str(get_path(vm, "storageProfile.imageReference.publisher", default="") or "")
    actual_offer = str(get_path(vm, "storageProfile.imageReference.offer", default="") or "")
    actual_sku = str(get_path(vm, "storageProfile.imageReference.sku", default="") or "")

    if actual_publisher and actual_publisher.lower() != expected_publisher.lower():
        conflicts.append(f"image publisher is {actual_publisher}, not {expected_publisher}")
    if actual_offer and actual_offer.lower() != expected_offer.lower():
        conflicts.append(f"image offer is {actual_offer}, not {expected_offer}")
    if actual_sku and actual_sku.lower() != expected_sku.lower():
        conflicts.append(f"image SKU is {actual_sku}, not {expected_sku}")
    return conflicts


def validate_existing_state(
    args: argparse.Namespace,
    subscription_name: str,
    subscription_id: str,
    tenant_id: str,
    rg: CommandResult,
    vnet: CommandResult,
    subnet: CommandResult,
    bastion: CommandResult,
    public_ip_resource: CommandResult,
    vm: CommandResult,
) -> int:
    failures = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal failures
        if ok:
            print(f"- OK: {label}")
            return
        failures += 1
        suffix = f": {detail}" if detail else ""
        print(f"- FAIL: {label}{suffix}")

    print("# Secure Windows VM Validation")
    print(f"\nSubscription: {subscription_name} ({subscription_id})")
    print(f"Tenant: {tenant_id or 'unknown'}")
    print(f"Expected resource group: {args.resource_group}")
    print(f"Expected VM: {args.vm_name}")
    print(f"Expected public IP: {args.public_ip_name}")

    check(rg.ok and isinstance(rg.data, dict), "resource group exists", args.resource_group)
    if rg.ok and isinstance(rg.data, dict):
        rg_location = str(rg.data.get("location") or "")
        check(rg_location.lower() == args.location.lower(), "resource group location matches", rg_location)

    check(vnet.ok and isinstance(vnet.data, dict), "virtual network exists", args.vnet_name)
    if vnet.ok and isinstance(vnet.data, dict):
        prefixes = get_path(vnet.data, "addressSpace.addressPrefixes", default=[])
        check(DEFAULT_VNET_PREFIX in prefixes, "virtual network address space matches", str(prefixes))

    check(subnet.ok and isinstance(subnet.data, dict), "VM subnet exists", args.subnet_name)
    if subnet.ok and isinstance(subnet.data, dict):
        prefix = str(get_path(subnet.data, "addressPrefix", default=""))
        prefixes = get_path(subnet.data, "addressPrefixes", default=[])
        check(
            prefix == DEFAULT_SUBNET_PREFIX or DEFAULT_SUBNET_PREFIX in as_list(prefixes),
            "VM subnet prefix matches",
            prefix or str(prefixes),
        )

    check(bastion.ok and isinstance(bastion.data, dict), "Bastion exists", args.bastion_name)
    if bastion.ok and isinstance(bastion.data, dict):
        sku = str(get_path(bastion.data, "sku.name", default="") or "")
        check(sku.lower() == "developer", "Bastion SKU is Developer", sku)

    check(public_ip_resource.ok and isinstance(public_ip_resource.data, dict), "VM public IP resource exists", args.public_ip_name)
    expected_public_ip_id = ""
    if public_ip_resource.ok and isinstance(public_ip_resource.data, dict):
        expected_public_ip_id = str(public_ip_resource.data.get("id") or "")
        sku = str(get_path(public_ip_resource.data, "sku.name", default="") or "")
        allocation = str(get_path(public_ip_resource.data, "publicIPAllocationMethod", default="") or "")
        check(sku.lower() == "standard", "VM public IP SKU is Standard", sku)
        check(allocation.lower() == "static", "VM public IP allocation is Static", allocation)

    check(vm.ok and isinstance(vm.data, dict), "VM exists", args.vm_name)
    nic_ids: list[str] = []
    private_ip = "unavailable"
    public_ip = "unavailable"
    if vm.ok and isinstance(vm.data, dict):
        private_ip = vm_private_ip(args.resource_group, args.vm_name)
        public_ip = vm_public_ip_address(args.resource_group, args.vm_name)
        vm_location = str(vm.data.get("location") or "")
        vm_size = str(get_path(vm.data, "hardwareProfile.vmSize", default="") or "")
        check(vm_location.lower() == args.location.lower(), "VM location matches", vm_location)
        check(vm_size.lower() == args.size.lower(), "VM size matches", vm_size)
        check(vm_has_public_ip(vm.data), "VM has a public IP for outbound internet")

        disk_conflicts = vm_os_disk_conflicts(
            vm.data,
            args.storage_sku,
            args.os_disk_size_gb,
            args.os_disk_delete_option,
        )
        check(not disk_conflicts, "VM OS disk settings match", "; ".join(disk_conflicts))

        image_conflicts = vm_image_conflicts(vm.data, args.image_sku)
        check(not image_conflicts, "VM image matches expected publisher/offer/SKU", "; ".join(image_conflicts))

        nic_ids = vm_nic_ids(vm.data)
        check(len(nic_ids) == 1, "VM has exactly one NIC", ", ".join(nic_ids) or "none")

    nic_data: dict[str, Any] | None = None
    if nic_ids:
        nic = show_nic_by_id(nic_ids[0])
        check(nic.ok and isinstance(nic.data, dict), "VM NIC can be read", nic_ids[0])
        if nic.ok and isinstance(nic.data, dict):
            nic_data = nic.data
            attached_public_ip_ids = nic_public_ip_ids(nic_data)
            check(
                bool(expected_public_ip_id)
                and any(resource_id_equals(public_ip_id, expected_public_ip_id) for public_ip_id in attached_public_ip_ids),
                "VM NIC is attached to expected public IP",
                ", ".join(resource_id_name(public_ip_id) for public_ip_id in attached_public_ip_ids) or "none",
            )
            expected_subnet_id = str(get_path(subnet.data, "id", default="") or "") if isinstance(subnet.data, dict) else ""
            attached_subnet_ids = nic_subnet_ids(nic_data)
            check(
                bool(expected_subnet_id)
                and any(resource_id_equals(subnet_id, expected_subnet_id) for subnet_id in attached_subnet_ids),
                "VM NIC is attached to expected subnet",
                ", ".join(resource_id_name(subnet_id) for subnet_id in attached_subnet_ids) or "none",
            )

    nsg_ids: list[str] = []
    if nic_data:
        nic_nsg_id = str(get_path(nic_data, "networkSecurityGroup.id", default="") or "")
        if nic_nsg_id:
            nsg_ids.append(nic_nsg_id)
    if subnet.ok and isinstance(subnet.data, dict):
        subnet_nsg_id = str(get_path(subnet.data, "networkSecurityGroup.id", default="") or "")
        if subnet_nsg_id:
            nsg_ids.append(subnet_nsg_id)

    nsg_ids = list(dict.fromkeys(nsg_ids))
    check(bool(nsg_ids), "NIC or subnet has an NSG attached", "no configured NSG found")

    nsg_rule_summaries: dict[str, list[str]] = {}
    public_rdp_rules: list[str] = []
    for nsg_id in nsg_ids:
        nsg = show_nsg_by_id(nsg_id)
        nsg_name = resource_id_name(nsg_id)
        check(nsg.ok and isinstance(nsg.data, dict), f"NSG can be read: {nsg_name}", nsg_id)
        if not nsg.ok or not isinstance(nsg.data, dict):
            continue
        nsg_rule_summaries[nsg_name] = [
            format_nsg_rule(rule)
            for rule in as_list(nsg.data.get("securityRules"))
            if isinstance(rule, dict)
        ]
        for rule in as_list(nsg.data.get("securityRules")):
            if isinstance(rule, dict) and rule_allows_public_rdp(rule):
                public_rdp_rules.append(f"{nsg_name}/{rule.get('name', 'unnamed')}")
    check(not public_rdp_rules, "no configured public inbound RDP allow rule", ", ".join(public_rdp_rules))

    print("\n## Network Security Group Rules")
    if not nsg_rule_summaries:
        print("- Nothing found. No readable configured NSG rules.")
    for nsg_name, rules in nsg_rule_summaries.items():
        print(f"- {nsg_name}:")
        if not rules:
            print("  - Nothing found. No custom security rules configured.")
            continue
        for rule in sorted(rules):
            print(f"  - {rule}")

    print("\n## VM IP Addresses")
    print(f"- VM private IP: {private_ip}")
    print(f"- VM public IP: {public_ip}")

    if failures:
        print(f"\nValidation failed with {failures} issue(s).")
        return 1

    print("\nValidation passed. Azure state matches the script's expected configuration.")
    return 0


def wait_for_group_deleted(resource_group: str, timeout_seconds: int = 1800) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        result = show_resource_group(resource_group)
        if resource_missing(result):
            return True
        if not result.ok:
            print(f"- unable to poll resource group deletion: {concise_error(result.error)}")
            return False
        print(f"- waiting for resource group deletion: {resource_group}")
        time.sleep(20)
    print(f"- timed out waiting for resource group deletion: {resource_group}")
    return False


def portal_vm_url(tenant_id: str, vm_id: str) -> str:
    tenant_part = f"@{tenant_id}/" if tenant_id else ""
    return f"https://portal.azure.com/#{tenant_part}resource{vm_id}/overview"


def vm_image(image_sku: str) -> str:
    return f"{DEFAULT_IMAGE_PUBLISHER}:{DEFAULT_IMAGE_OFFER}:{image_sku}:{DEFAULT_IMAGE_VERSION}"


def add_shared_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--location", default=DEFAULT_LOCATION, help=f"Azure region. Default: {DEFAULT_LOCATION}.")
    parser.add_argument(
        "--resource-group",
        default=DEFAULT_RESOURCE_GROUP,
        help=f"Script-owned resource group. Default: {DEFAULT_RESOURCE_GROUP}.",
    )
    parser.add_argument("--vm-name", default=DEFAULT_VM_NAME, help=f"VM name. Default: {DEFAULT_VM_NAME}.")
    parser.add_argument(
        "--admin-username",
        default=DEFAULT_ADMIN_USERNAME,
        help=f"Local Windows admin username. Default: {DEFAULT_ADMIN_USERNAME}.",
    )
    parser.add_argument(
        "--size",
        default=DEFAULT_VM_SIZE,
        help=(
            f"VM size. Default: {DEFAULT_VM_SIZE} (2 vCPU, 8 GiB RAM). "
            "Examples: Standard_B2als_v2=2 vCPU/4 GiB, Standard_B4as_v2=4 vCPU/16 GiB, "
            "Standard_B8as_v2=8 vCPU/32 GiB."
        ),
    )
    parser.add_argument(
        "--image-sku",
        default=DEFAULT_IMAGE_SKU,
        help=(
            f"Windows Server image SKU. Default: {DEFAULT_IMAGE_SKU}. "
            "Examples: 2025-datacenter-azure-edition-core, "
            "2025-datacenter-azure-edition-smalldisk, 2022-datacenter-azure-edition, "
            "2022-datacenter-azure-edition-core, 2022-datacenter-azure-edition-hotpatch."
        ),
    )
    parser.add_argument(
        "--storage-sku",
        default=DEFAULT_STORAGE_SKU,
        choices=[
            "Standard_LRS",
            "StandardSSD_LRS",
            "Premium_LRS",
            "UltraSSD_LRS",
            "Premium_ZRS",
            "StandardSSD_ZRS",
            "PremiumV2_LRS",
        ],
        help=f"Managed OS disk storage SKU. Default: {DEFAULT_STORAGE_SKU}.",
    )
    parser.add_argument(
        "--os-disk-size-gb",
        type=int,
        default=DEFAULT_OS_DISK_SIZE_GB,
        help=f"Managed OS disk size in GB. Default: {DEFAULT_OS_DISK_SIZE_GB}.",
    )
    parser.add_argument(
        "--os-disk-delete-option",
        default=DEFAULT_OS_DISK_DELETE_OPTION,
        choices=["Delete", "Detach"],
        help=f"What happens to the OS disk when the VM is deleted. Default: {DEFAULT_OS_DISK_DELETE_OPTION}.",
    )
    parser.add_argument("--public-ip-name", default=DEFAULT_VM_PUBLIC_IP_NAME, help=f"VM public IP name. Default: {DEFAULT_VM_PUBLIC_IP_NAME}.")
    parser.add_argument("--vnet-name", default=DEFAULT_VNET_NAME, help=f"Virtual network name. Default: {DEFAULT_VNET_NAME}.")
    parser.add_argument("--subnet-name", default=DEFAULT_SUBNET_NAME, help=f"VM subnet name. Default: {DEFAULT_SUBNET_NAME}.")
    parser.add_argument("--bastion-name", default=DEFAULT_BASTION_NAME, help=f"Bastion name. Default: {DEFAULT_BASTION_NAME}.")


def add_config_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config-script",
        default=DEFAULT_CONFIG_SCRIPT,
        help=f"Local PowerShell script to run with Azure VM Run Command. Default: {DEFAULT_CONFIG_SCRIPT}.",
    )
    parser.add_argument(
        "--run-command-name",
        default=DEFAULT_RUN_COMMAND_NAME,
        help=f"Managed VM Run Command resource name. Default: {DEFAULT_RUN_COMMAND_NAME}.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create and manage an idempotent Windows VM with outbound internet through a VM public IP "
            "and RDP through Azure Bastion Developer."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    dryrun = subparsers.add_parser("dryrun", help="Print expected resources and planned changes without changing Azure.")
    add_shared_options(dryrun)

    apply = subparsers.add_parser("apply", help="Create missing Azure resources and reuse matching existing resources.")
    add_shared_options(apply)
    add_config_options(apply)
    apply.add_argument("--configure", action="store_true", help="Run the guest configuration script after applying Azure resources.")

    validate = subparsers.add_parser("validate", help="Validate deployed Azure state against this script.")
    add_shared_options(validate)

    configure = subparsers.add_parser("configure", help="Run the guest configuration script against the existing VM.")
    add_shared_options(configure)
    add_config_options(configure)

    recreate = subparsers.add_parser("recreate", help="Plan or execute a full rebuild of the script-owned resource group.")
    add_shared_options(recreate)
    add_config_options(recreate)
    recreate.add_argument("--execute", action="store_true", help="Delete and recreate the resource group. Without this flag, recreate is a dry run.")
    recreate.add_argument("--configure", action="store_true", help="Run the guest configuration script after recreating Azure resources.")

    return parser


def inspect_deployment_state(args: argparse.Namespace) -> tuple[DeploymentState | None, int]:
    if not shutil.which("az"):
        return None, fail("Azure CLI executable `az` was not found on PATH.")

    account = az(["account", "show"], timeout=120)
    if not account.ok:
        return None, fail("Azure CLI is not logged in or cannot read the active account. Run `az login` and retry.", account)
    if not isinstance(account.data, dict):
        return None, fail("Azure CLI returned an unexpected account response.")

    subscription_id = str(account.data.get("id") or "")
    subscription_name = str(account.data.get("name") or subscription_id)
    tenant_id = str(account.data.get("tenantId") or "")
    if not subscription_id:
        return None, fail("Azure CLI default subscription did not include a subscription id.")

    rg = show_resource_group(args.resource_group)
    if ensure_no_unexpected_error("resource group", rg):
        return None, 1
    group_exists = rg.ok

    vnet = show_vnet(args.resource_group, args.vnet_name) if group_exists else CommandResult(False, [], error="NotFound")
    if group_exists and ensure_no_unexpected_error("virtual network", vnet):
        return None, 1

    subnet = (
        show_subnet(args.resource_group, args.vnet_name, args.subnet_name)
        if vnet.ok
        else CommandResult(False, [], error="NotFound")
    )
    if vnet.ok and ensure_no_unexpected_error("subnet", subnet):
        return None, 1

    bastion = show_bastion(args.resource_group, args.bastion_name) if group_exists else CommandResult(False, [], error="NotFound")
    if group_exists and ensure_no_unexpected_error("Bastion host", bastion):
        return None, 1

    public_ip_resource = (
        show_public_ip(args.resource_group, args.public_ip_name)
        if group_exists
        else CommandResult(False, [], error="NotFound")
    )
    if group_exists and ensure_no_unexpected_error("VM public IP", public_ip_resource):
        return None, 1

    vm = show_vm(args.resource_group, args.vm_name) if group_exists else CommandResult(False, [], error="NotFound")
    if group_exists and ensure_no_unexpected_error("virtual machine", vm):
        return None, 1

    return (
        DeploymentState(
            subscription_id=subscription_id,
            subscription_name=subscription_name,
            tenant_id=tenant_id,
            rg=rg,
            vnet=vnet,
            subnet=subnet,
            bastion=bastion,
            public_ip_resource=public_ip_resource,
            vm=vm,
        ),
        0,
    )


def validate_resource_compatibility(args: argparse.Namespace, state: DeploymentState) -> int:
    if state.rg.ok and isinstance(state.rg.data, dict):
        rg_location = str(state.rg.data.get("location") or "")
        if rg_location.lower() != args.location.lower():
            return fail(
                f"Resource group {args.resource_group} already exists in {rg_location}, "
                f"but requested location is {args.location}."
            )

    if state.vnet.ok and isinstance(state.vnet.data, dict):
        prefixes = get_path(state.vnet.data, "addressSpace.addressPrefixes", default=[])
        if DEFAULT_VNET_PREFIX not in prefixes:
            return fail(f"Virtual network {args.vnet_name} already exists but does not include {DEFAULT_VNET_PREFIX}.")

    if state.subnet.ok and isinstance(state.subnet.data, dict):
        prefix = str(get_path(state.subnet.data, "addressPrefix", default=""))
        prefixes = get_path(state.subnet.data, "addressPrefixes", default=[])
        if prefix != DEFAULT_SUBNET_PREFIX and DEFAULT_SUBNET_PREFIX not in as_list(prefixes):
            return fail(f"Subnet {args.subnet_name} already exists but does not use {DEFAULT_SUBNET_PREFIX}.")

    if state.bastion.ok and isinstance(state.bastion.data, dict):
        sku = str(get_path(state.bastion.data, "sku.name", default=""))
        if sku.lower() != "developer":
            return fail(f"Bastion {args.bastion_name} already exists with SKU {sku}, not Developer.")

    if state.public_ip_resource.ok and isinstance(state.public_ip_resource.data, dict):
        sku = str(get_path(state.public_ip_resource.data, "sku.name", default=""))
        allocation = str(get_path(state.public_ip_resource.data, "publicIPAllocationMethod", default=""))
        if sku.lower() != "standard":
            return fail(f"VM public IP {args.public_ip_name} already exists with SKU {sku}, not Standard.")
        if allocation.lower() != "static":
            return fail(f"VM public IP {args.public_ip_name} already exists with allocation {allocation}, not Static.")

    if state.vm.ok and isinstance(state.vm.data, dict):
        vm_location = str(state.vm.data.get("location") or "")
        vm_size = str(get_path(state.vm.data, "hardwareProfile.vmSize", default=""))
        if vm_location.lower() != args.location.lower():
            return fail(f"VM {args.vm_name} already exists in {vm_location}, not {args.location}.")
        if vm_size and vm_size.lower() != args.size.lower():
            return fail(f"VM {args.vm_name} already exists with size {vm_size}, not {args.size}.")
        if not vm_has_public_ip(state.vm.data):
            return fail(
                f"VM {args.vm_name} already exists without a public IP. "
                "Use `recreate --execute` to rebuild it with the current outbound setup."
            )
        if not state.public_ip_resource.ok:
            return fail(
                f"VM {args.vm_name} already exists with a public IP, but expected public IP "
                f"{args.public_ip_name} was not found. Use `recreate --execute` or use the existing public IP name."
            )

        nic_ids = vm_nic_ids(state.vm.data)
        if len(nic_ids) != 1:
            return fail(f"VM {args.vm_name} already exists with {len(nic_ids)} NICs, not one.")
        nic = show_nic_by_id(nic_ids[0])
        if not nic.ok or not isinstance(nic.data, dict):
            return fail(f"Unable to inspect VM NIC {resource_id_name(nic_ids[0])}.", nic)

        expected_public_ip_id = (
            str(state.public_ip_resource.data.get("id") or "")
            if isinstance(state.public_ip_resource.data, dict)
            else ""
        )
        attached_public_ip_ids = nic_public_ip_ids(nic.data)
        if not any(resource_id_equals(public_ip_id, expected_public_ip_id) for public_ip_id in attached_public_ip_ids):
            actual = ", ".join(resource_id_name(public_ip_id) for public_ip_id in attached_public_ip_ids) or "none"
            return fail(f"VM {args.vm_name} public IP attachment is {actual}, not {args.public_ip_name}.")

        disk_conflicts = vm_os_disk_conflicts(
            state.vm.data,
            args.storage_sku,
            args.os_disk_size_gb,
            args.os_disk_delete_option,
        )
        if disk_conflicts:
            return fail(f"VM {args.vm_name} already exists with incompatible disk settings: {'; '.join(disk_conflicts)}.")

        image_conflicts = vm_image_conflicts(state.vm.data, args.image_sku)
        if image_conflicts:
            return fail(f"VM {args.vm_name} already exists with incompatible image settings: {'; '.join(image_conflicts)}.")

    return 0


def build_resource_plan(state: DeploymentState) -> ResourcePlan:
    return ResourcePlan(
        needs_group=not state.rg.ok,
        needs_vnet=not state.vnet.ok,
        needs_subnet=not state.subnet.ok,
        needs_bastion=not state.bastion.ok,
        needs_vm_public_ip=not state.public_ip_resource.ok,
        needs_vm=not state.vm.ok,
    )


def print_resource_plan(args: argparse.Namespace, state: DeploymentState, mode: str, plan: ResourcePlan) -> None:
    print("# Secure Windows VM Plan")
    print(f"\nMode: {mode}")
    print(f"Subscription: {state.subscription_name} ({state.subscription_id})")
    print(f"Tenant: {state.tenant_id or 'unknown'}")
    print(f"Location: {args.location}")
    print(f"Resource group: {args.resource_group}")
    print(f"VM: {args.vm_name}")
    print(f"Image: {vm_image(args.image_sku)}")
    print(f"Size: {args.size}")
    print(f"OS disk: {args.os_disk_size_gb} GB {args.storage_sku}")
    print(f"OS disk delete option: {args.os_disk_delete_option}")
    print("Desktop access: Azure Bastion Developer portal RDP")
    print(f"VM public IP: {args.public_ip_name}")
    print("Outbound internet: VM public IP")
    print("Default public RDP NSG rule: none")

    print("\n## Planned Changes")
    if not plan.needs_anything:
        print("- Nothing changed. All expected resources already exist.")
        return
    if plan.needs_group:
        print(f"- create resource group {args.resource_group}")
    if plan.needs_vnet:
        print(f"- create virtual network {args.vnet_name} with subnet {args.subnet_name}")
    elif plan.needs_subnet:
        print(f"- create subnet {args.subnet_name}")
    if plan.needs_bastion:
        print(f"- create Bastion Developer host {args.bastion_name}")
    if plan.needs_vm_public_ip:
        print(f"- create Standard static public IP {args.public_ip_name} for VM outbound internet")
    if plan.needs_vm:
        print(f"- create Windows VM {args.vm_name} with public IP {args.public_ip_name}")


def print_connection(args: argparse.Namespace, state: DeploymentState, vm: dict[str, Any]) -> None:
    private_ip = vm_private_ip(args.resource_group, args.vm_name)
    public_ip = vm_public_ip_address(args.resource_group, args.vm_name)
    vm_id = str(vm.get("id") or "")

    print("\n## Connection")
    print(f"- Portal VM URL: {portal_vm_url(state.tenant_id, vm_id)}")
    print("- In the portal, open the VM and choose: Connect > Bastion.")
    print(f"- VM private IP: {private_ip}")
    print(f"- VM public IP: {public_ip}")
    print("- Public inbound RDP rule created by this script: none")
    print(f"- Username: {args.admin_username}")
    print(f"- Resource group: {args.resource_group}")
    print(f"- Subscription: {state.subscription_name} ({state.subscription_id})")
    print(f"- Tenant: {state.tenant_id or 'unknown'}")
    print("- Bastion Developer supports browser-based RDP only; no CLI tunnel/native client command is created.")


def apply_resources(args: argparse.Namespace, state: DeploymentState) -> tuple[DeploymentState | None, int, str | None]:
    if validate_resource_compatibility(args, state):
        return None, 1, None

    plan = build_resource_plan(state)
    print_resource_plan(args, state, "APPLY", plan)

    admin_password = prompt_password() if plan.needs_vm else ""

    print("\n## Execution")
    if plan.needs_group:
        result = run_step(
            f"create resource group {args.resource_group}",
            ["group", "create", "--name", args.resource_group, "--location", args.location],
            execute=True,
            timeout=180,
        )
        if not result.ok:
            return None, 1, None

    if plan.needs_vnet:
        result = run_step(
            f"create virtual network {args.vnet_name}",
            [
                "network",
                "vnet",
                "create",
                "--resource-group",
                args.resource_group,
                "--location",
                args.location,
                "--name",
                args.vnet_name,
                "--address-prefix",
                DEFAULT_VNET_PREFIX,
                "--subnet-name",
                args.subnet_name,
                "--subnet-prefixes",
                DEFAULT_SUBNET_PREFIX,
            ],
            execute=True,
            timeout=300,
        )
        if not result.ok:
            return None, 1, None
    elif plan.needs_subnet:
        result = run_step(
            f"create subnet {args.subnet_name}",
            [
                "network",
                "vnet",
                "subnet",
                "create",
                "--resource-group",
                args.resource_group,
                "--vnet-name",
                args.vnet_name,
                "--name",
                args.subnet_name,
                "--address-prefixes",
                DEFAULT_SUBNET_PREFIX,
            ],
            execute=True,
            timeout=300,
        )
        if not result.ok:
            return None, 1, None

    if plan.needs_bastion:
        result = run_step(
            f"create Bastion Developer host {args.bastion_name}",
            [
                "network",
                "bastion",
                "create",
                "--resource-group",
                args.resource_group,
                "--location",
                args.location,
                "--name",
                args.bastion_name,
                "--sku",
                "Developer",
                "--vnet-name",
                args.vnet_name,
            ],
            execute=True,
            timeout=1200,
        )
        if not result.ok:
            return None, fail(
                "Unable to create Azure Bastion Developer. If Developer SKU is unavailable in this region, "
                "rerun with a future Standard/Premium mode rather than silently creating a paid Bastion SKU.",
                result,
            ), None

    if plan.needs_vm_public_ip:
        result = run_step(
            f"create VM public IP {args.public_ip_name}",
            [
                "network",
                "public-ip",
                "create",
                "--resource-group",
                args.resource_group,
                "--location",
                args.location,
                "--name",
                args.public_ip_name,
                "--sku",
                "Standard",
                "--allocation-method",
                "Static",
            ],
            execute=True,
            timeout=300,
        )
        if not result.ok:
            return None, 1, None

    if plan.needs_vm:
        result = run_step(
            f"create Windows VM {args.vm_name}",
            [
                "vm",
                "create",
                "--resource-group",
                args.resource_group,
                "--location",
                args.location,
                "--name",
                args.vm_name,
                "--image",
                vm_image(args.image_sku),
                "--size",
                args.size,
                "--vnet-name",
                args.vnet_name,
                "--subnet",
                args.subnet_name,
                "--public-ip-address",
                args.public_ip_name,
                "--nsg-rule",
                "NONE",
                "--storage-sku",
                args.storage_sku,
                "--os-disk-size-gb",
                str(args.os_disk_size_gb),
                "--os-disk-delete-option",
                args.os_disk_delete_option,
                "--admin-username",
                args.admin_username,
                "--admin-password",
                admin_password,
            ],
            execute=True,
            timeout=1800,
        )
        if not result.ok:
            return None, 1, None

    refreshed_state, code = inspect_deployment_state(args)
    if code or not refreshed_state:
        return None, code or 1, None
    vm_after = refreshed_state.vm
    if not vm_after.ok or not isinstance(vm_after.data, dict):
        return None, fail("VM creation completed, but the VM could not be read afterward.", vm_after), None
    if not vm_has_public_ip(vm_after.data):
        return None, fail("VM exists but does not have a public IP for outbound internet access."), None

    print_connection(args, refreshed_state, vm_after.data)
    return refreshed_state, 0, admin_password or None


def resolved_config_script(path: str) -> Path:
    script = Path(path).expanduser()
    if not script.is_absolute():
        script = Path.cwd() / script
    return script


def run_command_messages(data: Json) -> list[str]:
    messages: list[str] = []
    if not isinstance(data, dict):
        return messages

    instance_view = data.get("instanceView")
    if isinstance(instance_view, dict):
        output = str(instance_view.get("output") or "").strip()
        error = str(instance_view.get("error") or "").strip()
        if output:
            messages.append(output.replace("\r\n", "\n"))
        if error:
            messages.append(error.replace("\r\n", "\n"))

        for status in as_list(instance_view.get("statuses")):
            if not isinstance(status, dict):
                continue
            message = str(status.get("message") or "").strip()
            if message:
                messages.append(message.replace("\r\n", "\n"))

    for item in as_list(data.get("value")):
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "").strip()
        if message:
            messages.append(message.replace("\r\n", "\n"))
    return messages


def run_command_has_error_output(data: Json) -> bool:
    if not isinstance(data, dict):
        return False

    instance_view = data.get("instanceView")
    if isinstance(instance_view, dict):
        error = str(instance_view.get("error") or "").strip()
        if error:
            return True
        execution_state = str(instance_view.get("executionState") or "").lower()
        if execution_state in {"failed", "canceled", "timedout"}:
            return True
        exit_code = instance_view.get("exitCode")
        if exit_code is not None:
            try:
                if int(exit_code) != 0:
                    return True
            except (TypeError, ValueError):
                return True

    for item in as_list(data.get("value")):
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "").strip()
        code = str(item.get("code") or "")
        if message and "stderr" in code.lower():
            return True
    return False


def print_run_command_output(data: Json) -> None:
    messages = run_command_messages(data)
    print("\n## Guest Configuration Output")
    if not messages:
        print("- Nothing found. Azure Run Command returned no output.")
        return

    for message in messages:
        print(message)


def prompt_run_as_password(username: str) -> str:
    return getpass.getpass(f"Windows admin password for {username}: ")


def run_guest_configuration(args: argparse.Namespace, run_as_password: str | None = None) -> int:
    vm = show_vm(args.resource_group, args.vm_name)
    if not vm.ok or not isinstance(vm.data, dict):
        return fail(f"VM {args.vm_name} does not exist or cannot be read. Run `apply` first.", vm)

    script = resolved_config_script(args.config_script)
    if not script.is_file():
        return fail(f"Configuration script was not found: {script}")

    existing_run_command = show_vm_run_command(args.resource_group, args.vm_name, args.run_command_name)
    if not existing_run_command.ok and not resource_missing(existing_run_command):
        return fail(f"Unable to inspect managed Run Command {args.run_command_name}.", existing_run_command)

    if run_as_password is None:
        run_as_password = prompt_run_as_password(args.admin_username)

    operation = "update" if existing_run_command.ok else "create"
    label = f"{operation} managed guest configuration run command {args.run_command_name}"
    print("\n## Guest Configuration")
    result = run_step(
        label,
        [
            "vm",
            "run-command",
            operation,
            "--resource-group",
            args.resource_group,
            "--vm-name",
            args.vm_name,
            "--location",
            args.location,
            "--run-command-name",
            args.run_command_name,
            "--script",
            f"@{script}",
            "--run-as-user",
            args.admin_username,
            "--run-as-password",
            run_as_password,
            "--timeout-in-seconds",
            "3600",
            "--async-execution",
            "false",
        ],
        execute=True,
        timeout=3900,
    )
    if not result.ok:
        return 1

    instance_view = show_vm_run_command_instance_view(args.resource_group, args.vm_name, args.run_command_name)
    if not instance_view.ok:
        return fail(f"Unable to read managed Run Command output for {args.run_command_name}.", instance_view)

    print_run_command_output(instance_view.data)
    if run_command_has_error_output(instance_view.data):
        return fail("Guest configuration failed. Azure Run Command returned stderr output.")
    print("- guest configuration completed")
    return 0


def handle_dryrun(args: argparse.Namespace) -> int:
    state, code = inspect_deployment_state(args)
    if code or not state:
        return code or 1
    if validate_resource_compatibility(args, state):
        return 1
    print_resource_plan(args, state, "DRY RUN", build_resource_plan(state))
    print("\nDry run only. Nothing changed.")
    return 0


def handle_apply(args: argparse.Namespace) -> int:
    state, code = inspect_deployment_state(args)
    if code or not state:
        return code or 1
    _, code, admin_password = apply_resources(args, state)
    if code:
        return code
    if args.configure:
        return run_guest_configuration(args, admin_password)
    return 0


def handle_validate(args: argparse.Namespace) -> int:
    state, code = inspect_deployment_state(args)
    if code or not state:
        return code or 1
    return validate_existing_state(
        args,
        state.subscription_name,
        state.subscription_id,
        state.tenant_id,
        state.rg,
        state.vnet,
        state.subnet,
        state.bastion,
        state.public_ip_resource,
        state.vm,
    )


def handle_configure(args: argparse.Namespace) -> int:
    state, code = inspect_deployment_state(args)
    if code or not state:
        return code or 1
    if not state.vm.ok or not isinstance(state.vm.data, dict):
        return fail(f"VM {args.vm_name} does not exist or cannot be read. Run `apply` first.", state.vm)
    return run_guest_configuration(args)


def handle_recreate(args: argparse.Namespace) -> int:
    state, code = inspect_deployment_state(args)
    if code or not state:
        return code or 1

    print("# Secure Windows VM Recreate Plan")
    print(f"\nMode: {'RECREATE EXECUTE' if args.execute else 'RECREATE DRY RUN'}")
    print(f"Subscription: {state.subscription_name} ({state.subscription_id})")
    print(f"Tenant: {state.tenant_id or 'unknown'}")
    print(f"Location: {args.location}")
    print(f"Resource group: {args.resource_group}")
    print("\n## Recreate")
    if state.rg.ok:
        print(f"- {'delete' if args.execute else 'would delete'} resource group {args.resource_group}")
    else:
        print("- existing resource group: Nothing found.")
    if not args.execute:
        print("- would recreate the VM stack after deletion.")
        print("\nDry run only. Nothing changed.")
        return 0

    if state.rg.ok:
        deleted = run_step(
            f"delete resource group {args.resource_group}",
            ["group", "delete", "--name", args.resource_group, "--yes", "--no-wait"],
            execute=True,
            timeout=180,
        )
        if not deleted.ok:
            return 1
        if not wait_for_group_deleted(args.resource_group):
            return 1

    missing_state = DeploymentState(
        subscription_id=state.subscription_id,
        subscription_name=state.subscription_name,
        tenant_id=state.tenant_id,
        rg=CommandResult(False, ["az", "group", "show"], error="ResourceNotFound"),
        vnet=CommandResult(False, [], error="NotFound"),
        subnet=CommandResult(False, [], error="NotFound"),
        bastion=CommandResult(False, [], error="NotFound"),
        public_ip_resource=CommandResult(False, [], error="NotFound"),
        vm=CommandResult(False, [], error="NotFound"),
    )
    _, code, admin_password = apply_resources(args, missing_state)
    if code:
        return code
    if args.configure:
        return run_guest_configuration(args, admin_password)
    return 0


def main() -> int:
    old_flags = {"--execute", "--validate", "--recreate"}
    if len(sys.argv) > 1 and sys.argv[1] in old_flags:
        return fail(
            "Top-level flags were removed. Use `dryrun`, `apply`, `validate`, `configure`, or `recreate` subcommands."
        )

    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "dryrun": handle_dryrun,
        "apply": handle_apply,
        "validate": handle_validate,
        "configure": handle_configure,
        "recreate": handle_recreate,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
