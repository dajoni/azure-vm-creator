#!/usr/bin/env python3
"""Create a secure Windows VM reachable through Azure Bastion Developer.

The VM is created without a public IP address. Azure CLI's default subscription
from `az account show` is used as the deployment target.
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
from typing import Any


Json = dict[str, Any] | list[Any] | str | int | float | bool | None

DEFAULT_LOCATION = "northeurope"
DEFAULT_RESOURCE_GROUP = "rg-secure-winvm"
DEFAULT_VM_NAME = "vm-secure-win"
DEFAULT_ADMIN_USERNAME = "azureuser"
DEFAULT_VM_SIZE = "Standard_B2as_v2"
DEFAULT_IMAGE = "MicrosoftWindowsServer:WindowsServer:2025-datacenter-azure-edition:latest"
DEFAULT_STORAGE_SKU = "StandardSSD_LRS"
DEFAULT_OS_DISK_SIZE_GB = 127
DEFAULT_OS_DISK_DELETE_OPTION = "Delete"
DEFAULT_VNET_NAME = "vnet-secure-win"
DEFAULT_SUBNET_NAME = "subnet-secure-win"
DEFAULT_BASTION_NAME = "bastion-secure-win"
DEFAULT_NAT_GATEWAY_NAME = "nat-secure-win"
DEFAULT_NAT_PUBLIC_IP_NAME = "pip-nat-secure-win"
DEFAULT_VNET_PREFIX = "10.42.0.0/16"
DEFAULT_SUBNET_PREFIX = "10.42.1.0/24"
DEFAULT_NAT_IDLE_TIMEOUT_MINUTES = 4


@dataclass
class CommandResult:
    ok: bool
    command: list[str]
    data: Json = None
    error: str = ""


SENSITIVE_FLAGS = {"--admin-password", "--password", "--secret", "--value"}


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


def show_nat_gateway(resource_group: str, name: str) -> CommandResult:
    return az(["network", "nat", "gateway", "show", "--resource-group", resource_group, "--name", name], timeout=120)


def show_vm(resource_group: str, name: str) -> CommandResult:
    return az(["vm", "show", "--resource-group", resource_group, "--name", name, "--show-details"], timeout=180)


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


def vm_has_public_ip(vm: dict[str, Any]) -> bool:
    public_ips = get_path(vm, "publicIps", default="")
    return bool(str(public_ips or "").strip())


def resource_id_name(resource_id: str) -> str:
    return resource_id.rstrip("/").split("/")[-1] if resource_id else ""


def subnet_nat_gateway_name(subnet: dict[str, Any]) -> str:
    nat_gateway_id = str(get_path(subnet, "natGateway.id", default="") or "")
    return resource_id_name(nat_gateway_id)


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create an idempotent private Windows VM reachable through Azure Bastion Developer."
    )
    parser.add_argument("--execute", action="store_true", help="Actually create or recreate resources. Default is dry-run.")
    parser.add_argument("--recreate", action="store_true", help="Delete the script-owned resource group and recreate it.")
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
    parser.add_argument("--size", default=DEFAULT_VM_SIZE, help=f"VM size. Default: {DEFAULT_VM_SIZE}.")
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
    parser.add_argument("--vnet-name", default=DEFAULT_VNET_NAME, help=argparse.SUPPRESS)
    parser.add_argument("--subnet-name", default=DEFAULT_SUBNET_NAME, help=argparse.SUPPRESS)
    parser.add_argument("--bastion-name", default=DEFAULT_BASTION_NAME, help=argparse.SUPPRESS)
    parser.add_argument("--nat-gateway-name", default=DEFAULT_NAT_GATEWAY_NAME, help=argparse.SUPPRESS)
    parser.add_argument("--nat-public-ip-name", default=DEFAULT_NAT_PUBLIC_IP_NAME, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if not shutil.which("az"):
        return fail("Azure CLI executable `az` was not found on PATH.")

    account = az(["account", "show"], timeout=120)
    if not account.ok:
        return fail("Azure CLI is not logged in or cannot read the active account. Run `az login` and retry.", account)
    if not isinstance(account.data, dict):
        return fail("Azure CLI returned an unexpected account response.")

    subscription_id = str(account.data.get("id") or "")
    subscription_name = str(account.data.get("name") or subscription_id)
    tenant_id = str(account.data.get("tenantId") or "")
    if not subscription_id:
        return fail("Azure CLI default subscription did not include a subscription id.")

    rg = show_resource_group(args.resource_group)
    if ensure_no_unexpected_error("resource group", rg):
        return 1
    group_exists = rg.ok

    if args.recreate:
        print("# Secure Windows VM Plan")
        print(f"\nMode: {'EXECUTE' if args.execute else 'DRY RUN'}")
        print(f"Subscription: {subscription_name} ({subscription_id})")
        print(f"Tenant: {tenant_id or 'unknown'}")
        print(f"Location: {args.location}")
        print(f"Resource group: {args.resource_group}")
        print("\n## Recreate")
        if group_exists:
            print(f"- {'delete' if args.execute else 'would delete'} resource group {args.resource_group}")
        else:
            print("- existing resource group: Nothing found.")
        if not args.execute:
            print("- would recreate the VM stack after deletion.")
            print("\nDry run only. Nothing changed.")
            return 0

        if group_exists:
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
        group_exists = False
        rg = CommandResult(False, ["az", "group", "show"], error="ResourceNotFound")

    if group_exists and isinstance(rg.data, dict):
        rg_location = str(rg.data.get("location") or "")
        if rg_location.lower() != args.location.lower():
            return fail(
                f"Resource group {args.resource_group} already exists in {rg_location}, "
                f"but requested location is {args.location}."
            )

    vnet = show_vnet(args.resource_group, args.vnet_name) if group_exists else CommandResult(False, [], error="NotFound")
    if group_exists and ensure_no_unexpected_error("virtual network", vnet):
        return 1

    subnet = (
        show_subnet(args.resource_group, args.vnet_name, args.subnet_name)
        if vnet.ok
        else CommandResult(False, [], error="NotFound")
    )
    if vnet.ok and ensure_no_unexpected_error("subnet", subnet):
        return 1

    bastion = show_bastion(args.resource_group, args.bastion_name) if group_exists else CommandResult(False, [], error="NotFound")
    if group_exists and ensure_no_unexpected_error("Bastion host", bastion):
        return 1

    nat_public_ip = (
        show_public_ip(args.resource_group, args.nat_public_ip_name)
        if group_exists
        else CommandResult(False, [], error="NotFound")
    )
    if group_exists and ensure_no_unexpected_error("NAT public IP", nat_public_ip):
        return 1

    nat_gateway = (
        show_nat_gateway(args.resource_group, args.nat_gateway_name)
        if group_exists
        else CommandResult(False, [], error="NotFound")
    )
    if group_exists and ensure_no_unexpected_error("NAT Gateway", nat_gateway):
        return 1

    vm = show_vm(args.resource_group, args.vm_name) if group_exists else CommandResult(False, [], error="NotFound")
    if group_exists and ensure_no_unexpected_error("virtual machine", vm):
        return 1

    if vnet.ok and isinstance(vnet.data, dict):
        prefixes = get_path(vnet.data, "addressSpace.addressPrefixes", default=[])
        if DEFAULT_VNET_PREFIX not in prefixes:
            return fail(
                f"Virtual network {args.vnet_name} already exists but does not include {DEFAULT_VNET_PREFIX}."
            )
    if subnet.ok and isinstance(subnet.data, dict):
        prefix = str(get_path(subnet.data, "addressPrefix", default=""))
        prefixes = get_path(subnet.data, "addressPrefixes", default=[])
        if prefix != DEFAULT_SUBNET_PREFIX and DEFAULT_SUBNET_PREFIX not in as_list(prefixes):
            return fail(
                f"Subnet {args.subnet_name} already exists but does not use {DEFAULT_SUBNET_PREFIX}."
            )
    if bastion.ok and isinstance(bastion.data, dict):
        sku = str(get_path(bastion.data, "sku.name", default=""))
        if sku.lower() != "developer":
            return fail(f"Bastion {args.bastion_name} already exists with SKU {sku}, not Developer.")
    if nat_public_ip.ok and isinstance(nat_public_ip.data, dict):
        sku = str(get_path(nat_public_ip.data, "sku.name", default=""))
        allocation = str(get_path(nat_public_ip.data, "publicIPAllocationMethod", default=""))
        if sku.lower() != "standard":
            return fail(f"NAT public IP {args.nat_public_ip_name} already exists with SKU {sku}, not Standard.")
        if allocation.lower() != "static":
            return fail(
                f"NAT public IP {args.nat_public_ip_name} already exists with allocation {allocation}, not Static."
            )
    if nat_gateway.ok and isinstance(nat_gateway.data, dict):
        sku = str(get_path(nat_gateway.data, "sku.name", default=""))
        if sku.lower() != "standard":
            return fail(f"NAT Gateway {args.nat_gateway_name} already exists with SKU {sku}, not Standard.")
    if subnet.ok and isinstance(subnet.data, dict) and nat_gateway.ok:
        actual_nat_name = subnet_nat_gateway_name(subnet.data)
        if actual_nat_name and actual_nat_name.lower() != args.nat_gateway_name.lower():
            return fail(
                f"Subnet {args.subnet_name} already uses NAT Gateway {actual_nat_name}, not {args.nat_gateway_name}."
            )
    if vm.ok and isinstance(vm.data, dict):
        vm_location = str(vm.data.get("location") or "")
        vm_size = str(get_path(vm.data, "hardwareProfile.vmSize", default=""))
        if vm_location.lower() != args.location.lower():
            return fail(f"VM {args.vm_name} already exists in {vm_location}, not {args.location}.")
        if vm_size and vm_size.lower() != args.size.lower():
            return fail(f"VM {args.vm_name} already exists with size {vm_size}, not {args.size}.")
        if vm_has_public_ip(vm.data):
            return fail(f"VM {args.vm_name} already exists with a public IP. Refusing to reuse it.")
        disk_conflicts = vm_os_disk_conflicts(
            vm.data,
            args.storage_sku,
            args.os_disk_size_gb,
            args.os_disk_delete_option,
        )
        if disk_conflicts:
            return fail(f"VM {args.vm_name} already exists with incompatible disk settings: {'; '.join(disk_conflicts)}.")

    needs_group = not group_exists
    needs_vnet = not vnet.ok
    needs_subnet = not subnet.ok
    needs_bastion = not bastion.ok
    needs_nat_public_ip = not nat_public_ip.ok
    needs_nat_gateway = not nat_gateway.ok
    needs_nat_association = not (
        subnet.ok
        and isinstance(subnet.data, dict)
        and subnet_nat_gateway_name(subnet.data).lower() == args.nat_gateway_name.lower()
    )
    needs_vm = not vm.ok
    needs_anything = (
        needs_group
        or needs_vnet
        or needs_subnet
        or needs_bastion
        or needs_nat_public_ip
        or needs_nat_gateway
        or needs_nat_association
        or needs_vm
    )

    print("# Secure Windows VM Plan")
    print(f"\nMode: {'EXECUTE' if args.execute else 'DRY RUN'}")
    print(f"Subscription: {subscription_name} ({subscription_id})")
    print(f"Tenant: {tenant_id or 'unknown'}")
    print(f"Location: {args.location}")
    print(f"Resource group: {args.resource_group}")
    print(f"VM: {args.vm_name}")
    print(f"Image: {DEFAULT_IMAGE}")
    print(f"Size: {args.size}")
    print(f"OS disk: {args.os_disk_size_gb} GB {args.storage_sku}")
    print(f"OS disk delete option: {args.os_disk_delete_option}")
    print("Desktop access: Azure Bastion Developer portal RDP")
    print("VM public IP: none")
    print(f"Outbound internet: NAT Gateway {args.nat_gateway_name} via public IP {args.nat_public_ip_name}")
    print("Default public RDP NSG rule: none")

    print("\n## Planned Changes")
    if not needs_anything:
        print("- Nothing changed. All expected resources already exist.")
    else:
        if needs_group:
            print(f"- create resource group {args.resource_group}")
        if needs_vnet:
            print(f"- create virtual network {args.vnet_name} with subnet {args.subnet_name}")
        elif needs_subnet:
            print(f"- create subnet {args.subnet_name}")
        if needs_bastion:
            print(f"- create Bastion Developer host {args.bastion_name}")
        if needs_nat_public_ip:
            print(f"- create Standard static public IP {args.nat_public_ip_name} for NAT Gateway egress")
        if needs_nat_gateway:
            print(f"- create NAT Gateway {args.nat_gateway_name}")
        if needs_nat_association:
            print(f"- associate NAT Gateway {args.nat_gateway_name} with subnet {args.subnet_name}")
        if needs_vm:
            print(f"- create Windows VM {args.vm_name} without a public IP")

    if not args.execute:
        print("\nDry run only. Nothing changed.")
        print("To create or update the VM stack, rerun with `--execute`.")
        if args.recreate:
            print("To rebuild from scratch, rerun with `--recreate --execute`.")
        return 0

    admin_password = prompt_password() if needs_vm else ""

    print("\n## Execution")
    if needs_group:
        result = run_step(
            f"create resource group {args.resource_group}",
            ["group", "create", "--name", args.resource_group, "--location", args.location],
            execute=True,
            timeout=180,
        )
        if not result.ok:
            return 1

    if needs_vnet:
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
            return 1
    elif needs_subnet:
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
            return 1

    if needs_bastion:
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
            return fail(
                "Unable to create Azure Bastion Developer. If Developer SKU is unavailable in this region, "
                "rerun with a future Standard/Premium mode rather than silently creating a paid Bastion SKU.",
                result,
            )

    if needs_nat_public_ip:
        result = run_step(
            f"create NAT public IP {args.nat_public_ip_name}",
            [
                "network",
                "public-ip",
                "create",
                "--resource-group",
                args.resource_group,
                "--location",
                args.location,
                "--name",
                args.nat_public_ip_name,
                "--sku",
                "Standard",
                "--allocation-method",
                "Static",
            ],
            execute=True,
            timeout=300,
        )
        if not result.ok:
            return 1

    if needs_nat_gateway:
        result = run_step(
            f"create NAT Gateway {args.nat_gateway_name}",
            [
                "network",
                "nat",
                "gateway",
                "create",
                "--resource-group",
                args.resource_group,
                "--location",
                args.location,
                "--name",
                args.nat_gateway_name,
                "--sku",
                "Standard",
                "--public-ip-addresses",
                args.nat_public_ip_name,
                "--idle-timeout",
                str(DEFAULT_NAT_IDLE_TIMEOUT_MINUTES),
            ],
            execute=True,
            timeout=600,
        )
        if not result.ok:
            return 1

    if needs_nat_association:
        result = run_step(
            f"associate NAT Gateway {args.nat_gateway_name} with subnet {args.subnet_name}",
            [
                "network",
                "vnet",
                "subnet",
                "update",
                "--resource-group",
                args.resource_group,
                "--vnet-name",
                args.vnet_name,
                "--name",
                args.subnet_name,
                "--nat-gateway",
                args.nat_gateway_name,
            ],
            execute=True,
            timeout=300,
        )
        if not result.ok:
            return 1

    if needs_vm:
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
                DEFAULT_IMAGE,
                "--size",
                args.size,
                "--vnet-name",
                args.vnet_name,
                "--subnet",
                args.subnet_name,
                "--public-ip-address",
                "",
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
            return 1

    vm_after = show_vm(args.resource_group, args.vm_name)
    if not vm_after.ok or not isinstance(vm_after.data, dict):
        return fail("VM creation completed, but the VM could not be read afterward.", vm_after)
    if vm_has_public_ip(vm_after.data):
        return fail("VM exists but has a public IP. This violates the secure desktop configuration.")

    private_ip = vm_private_ip(args.resource_group, args.vm_name)
    vm_id = str(vm_after.data.get("id") or "")

    print("\n## Connection")
    print(f"- Portal VM URL: {portal_vm_url(tenant_id, vm_id)}")
    print("- In the portal, open the VM and choose: Connect > Bastion.")
    print(f"- Private IP: {private_ip}")
    print(f"- Outbound internet: NAT Gateway {args.nat_gateway_name}")
    print(f"- Username: {args.admin_username}")
    print(f"- Resource group: {args.resource_group}")
    print(f"- Subscription: {subscription_name} ({subscription_id})")
    print(f"- Tenant: {tenant_id or 'unknown'}")
    print("- Bastion Developer supports browser-based RDP only; no CLI tunnel/native client command is created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
