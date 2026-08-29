# Azure Windows VM Utility

This workspace contains one script:

- `scripts/azure_windows_vm.py`

It creates an idempotent private Windows Server VM in the current Azure CLI default subscription. The VM has no public IP address and is intended to be reached through Azure Bastion Developer using browser-based RDP in the Azure portal.

## Prerequisites

- Azure CLI installed.
- Logged in with `az login`.
- The desired subscription selected with `az account set --subscription "<subscription-id-or-name>"`.
- Permission to create resource groups, networking resources, Bastion, NAT Gateway, public IPs, disks, and virtual machines.

## Usage

Dry run, which prints the plan and changes nothing:

```bash
python3 scripts/azure_windows_vm.py
```

Create or complete missing resources:

```bash
python3 scripts/azure_windows_vm.py --execute
```

Rebuild the script-owned resource group from scratch:

```bash
python3 scripts/azure_windows_vm.py --recreate --execute
```

Useful overrides:

```bash
python3 scripts/azure_windows_vm.py --location northeurope
python3 scripts/azure_windows_vm.py --resource-group rg-secure-winvm
python3 scripts/azure_windows_vm.py --vm-name vm-secure-win
python3 scripts/azure_windows_vm.py --admin-username azureuser
python3 scripts/azure_windows_vm.py --size Standard_B2as_v2
python3 scripts/azure_windows_vm.py --storage-sku StandardSSD_LRS
python3 scripts/azure_windows_vm.py --os-disk-size-gb 127
python3 scripts/azure_windows_vm.py --os-disk-delete-option Delete
```

## Defaults

- Region: `northeurope`
- Resource group: `rg-secure-winvm`
- VM name: `vm-secure-win`
- Image: `MicrosoftWindowsServer:WindowsServer:2025-datacenter-azure-edition:latest`
- Size: `Standard_B2as_v2` with 2 vCPU and 8 GiB RAM
- OS disk: `127` GB
- OS disk storage: `StandardSSD_LRS`
- OS disk delete option: `Delete`
- VNet: `vnet-secure-win`
- Subnet: `subnet-secure-win`
- Address space: `10.42.0.0/16`
- VM subnet prefix: `10.42.1.0/24`
- Bastion: Developer SKU
- VM public IP: none
- Outbound internet: NAT Gateway with a Standard static public IP attached to the VM subnet
- Default public RDP NSG rule: none

## Connection

After a successful run, the script prints:

- Azure portal VM URL
- Bastion connection instruction: open the VM, then choose `Connect > Bastion`
- VM private IP
- Admin username
- Resource group
- Subscription
- Tenant

Bastion Developer supports portal-based browser RDP only. The script does not create a local RDP tunnel or native RDP client connection.

## Password Handling

The script prompts for the local Windows admin password only when it needs to create the VM. It does not write the password to repo files, and command output redacts sensitive arguments.

One caveat: Azure CLI receives the password as an `az vm create --admin-password` process argument during VM creation. Avoid running this from a shared machine where local process arguments may be visible to other users.

## Safety

- Default mode is dry-run.
- `--execute` is required before Azure resources are created.
- `--recreate --execute` deletes and recreates the configured resource group.
- The script does not delete the Azure subscription.
- The script does not create or delete tenant-wide Entra ID objects.

## Validation

Run:

```bash
python3 -m py_compile scripts/azure_windows_vm.py
python3 scripts/azure_windows_vm.py --help
```
