# Azure Windows VM Utility

This workspace contains:

- `scripts/azure_windows_vm.py`
- `scripts/configure_windows_vm.ps1`

It creates an idempotent Windows Server VM in the current Azure CLI default subscription. The VM has a public IP address for outbound internet access, but the script does not create a public inbound RDP rule. Desktop access is intended to go through Azure Bastion Developer using browser-based RDP in the Azure portal.

## Prerequisites

- Azure CLI installed.
- Logged in with `az login`.
- The desired subscription selected with `az account set --subscription "<subscription-id-or-name>"`.
- Permission to create resource groups, networking resources, Bastion, public IPs, disks, and virtual machines.

## Usage

Dry run, which prints expected resources and changes nothing:

```bash
python3 scripts/azure_windows_vm.py dryrun
```

Create or complete missing resources:

```bash
python3 scripts/azure_windows_vm.py apply
```

Create or complete missing resources, then stage desktop guest configuration:

```bash
python3 scripts/azure_windows_vm.py apply --configure
```

Validate the deployed Azure state against the script's expected configuration:

```bash
python3 scripts/azure_windows_vm.py validate
```

Stage desktop guest configuration against an existing VM:

```bash
python3 scripts/azure_windows_vm.py configure
```

Preview a full rebuild of the script-owned resource group:

```bash
python3 scripts/azure_windows_vm.py recreate
```

Rebuild the script-owned resource group from scratch:

```bash
python3 scripts/azure_windows_vm.py recreate --execute
python3 scripts/azure_windows_vm.py recreate --execute --configure
```

Preview removal of the script-owned resource group:

```bash
python3 scripts/azure_windows_vm.py teardown
```

Remove the script-owned resource group:

```bash
python3 scripts/azure_windows_vm.py teardown --execute
python3 scripts/azure_windows_vm.py teardown --resource-group rg-secure-winvm --execute
```

Shared options are available on `dryrun`, `apply`, `validate`, `configure`, `recreate`, and `teardown`:

```bash
python3 scripts/azure_windows_vm.py apply --location northeurope
python3 scripts/azure_windows_vm.py apply --resource-group rg-secure-winvm
python3 scripts/azure_windows_vm.py apply --vm-name vm-secure-win
python3 scripts/azure_windows_vm.py apply --admin-username azureuser
python3 scripts/azure_windows_vm.py apply --storage-sku StandardSSD_LRS
python3 scripts/azure_windows_vm.py apply --os-disk-size-gb 127
python3 scripts/azure_windows_vm.py apply --os-disk-delete-option Delete
python3 scripts/azure_windows_vm.py apply --public-ip-name pip-secure-win
python3 scripts/azure_windows_vm.py apply --vnet-name vnet-secure-win
python3 scripts/azure_windows_vm.py apply --subnet-name subnet-secure-win
python3 scripts/azure_windows_vm.py apply --bastion-name bastion-secure-win
```

VM size examples:

```bash
python3 scripts/azure_windows_vm.py apply --size Standard_B2als_v2  # 2 vCPU, 4 GiB RAM
python3 scripts/azure_windows_vm.py apply --size Standard_B2as_v2   # 2 vCPU, 8 GiB RAM
python3 scripts/azure_windows_vm.py apply --size Standard_B4as_v2   # 4 vCPU, 16 GiB RAM
python3 scripts/azure_windows_vm.py apply --size Standard_B8as_v2   # 8 vCPU, 32 GiB RAM
```

Windows Server image SKU examples:

```bash
python3 scripts/azure_windows_vm.py apply --image-sku 2025-datacenter-azure-edition
python3 scripts/azure_windows_vm.py apply --image-sku 2025-datacenter-azure-edition-core
python3 scripts/azure_windows_vm.py apply --image-sku 2025-datacenter-azure-edition-smalldisk
python3 scripts/azure_windows_vm.py apply --image-sku 2022-datacenter-azure-edition
python3 scripts/azure_windows_vm.py apply --image-sku 2022-datacenter-azure-edition-core
python3 scripts/azure_windows_vm.py apply --image-sku 2022-datacenter-azure-edition-hotpatch
```

Guest configuration staging options are available on `apply`, `configure`, and `recreate`:

```bash
python3 scripts/azure_windows_vm.py configure --config-script scripts/configure_windows_vm.ps1
python3 scripts/azure_windows_vm.py configure --run-command-name configure-windows-vm
```

`--config-script` points to the local PowerShell staging script. The staging script is copied through Azure Run Command, writes the desktop installer to `C:\ProgramData\AzureVmCreator\configure_windows_vm.ps1`, and registers a one-time scheduled task for the next interactive `azureuser` logon.

`--run-command-name` is used as the base name for a temporary managed Run Command resource. The script appends a timestamp and random suffix for each staging run.

## Defaults

- Region: `northeurope`
- Resource group: `rg-secure-winvm`
- VM name: `vm-secure-win`
- Image: `MicrosoftWindowsServer:WindowsServer:2025-datacenter-azure-edition:latest`
- Image SKU: `2025-datacenter-azure-edition`
- Size: `Standard_B2as_v2` with 2 vCPU and 8 GiB RAM
- OS disk: `127` GB
- OS disk storage: `StandardSSD_LRS`
- OS disk delete option: `Delete`
- VNet: `vnet-secure-win`
- Subnet: `subnet-secure-win`
- Address space: `10.42.0.0/16`
- VM subnet prefix: `10.42.1.0/24`
- Bastion: Developer SKU
- VM public IP: Standard static public IP named `pip-secure-win`
- Outbound internet: VM public IP
- Default public RDP NSG rule: none
- Guest configuration staging script: `scripts/configure_windows_vm.ps1`
- Managed Run Command base name: `configure-windows-vm`

## Connection

After a successful run, the script prints:

- Azure portal VM URL
- Bastion connection instruction: open the VM, then choose `Connect > Bastion`
- VM private IP
- VM public IP
- Admin username
- Resource group
- Subscription
- Tenant

Bastion Developer supports portal-based browser RDP only. The script does not create a local RDP tunnel or native RDP client connection.

The public IP is present so the VM can initiate outbound internet connections. The script still passes `--nsg-rule NONE` to `az vm create`, so Azure CLI does not add a public inbound RDP rule. Do not add an `Internet -> 3389` NSG rule unless you intentionally want direct public RDP exposure.

Use `validate` to check that the deployed resources still match the script, including the VM size, disk settings, image publisher/offer/SKU, expected public IP attachment, Bastion SKU, VNet/subnet prefixes, and absence of a configured public inbound RDP allow rule on the VM NIC or subnet NSG. Validation also prints all configured custom NSG rules plus the VM private and public IP addresses when the VM can be read.

Use `configure`, `apply --configure`, or `recreate --execute --configure` to stage guest configuration through a temporary managed Azure VM Run Command resource. This does not require public inbound RDP or WinRM. Azure Run Command runs only the staging step in the default managed context, prints the staging output, then deletes the temporary managed Run Command resource.

The staged task is named `AzureVmCreator-ConfigureDesktop`. It runs at the next interactive logon for the configured admin user and starts a visible PowerShell window with:

```powershell
powershell.exe -NoExit -ExecutionPolicy Bypass -File C:\ProgramData\AzureVmCreator\configure_windows_vm.ps1
```

The desktop installer writes `C:\ProgramData\AzureVmCreator\configure.log`, installs ChatGPT, Claude, and Git through `winget`, writes `C:\ProgramData\AzureVmCreator\configured.txt` after success, and removes the scheduled task only after all installs succeed. Failed installs leave the task in place for retry at the next login.

## Password Handling

The script prompts for the local Windows admin password only when it needs to create the VM. Guest configuration staging does not require or store the Windows password. It registers an interactive logon task for the configured admin user instead.

One caveat: Azure CLI receives the password as a process argument during VM creation. Avoid running VM creation from a shared machine where local process arguments may be visible to other users.

## Safety

- `dryrun` is read-only.
- `apply` creates Azure resources.
- `recreate` is read-only unless `--execute` is passed.
- `recreate --execute` deletes and recreates the configured resource group.
- `teardown` is read-only unless `--execute` is passed.
- `teardown --execute` deletes the configured resource group only.
- The script does not delete the Azure subscription.
- The script does not create or delete tenant-wide Entra ID objects.

## Validation

Run:

```bash
python3 -m py_compile scripts/azure_windows_vm.py
python3 scripts/azure_windows_vm.py --help
python3 scripts/azure_windows_vm.py dryrun --help
python3 scripts/azure_windows_vm.py apply --help
python3 scripts/azure_windows_vm.py validate --help
python3 scripts/azure_windows_vm.py configure --help
python3 scripts/azure_windows_vm.py recreate --help
python3 scripts/azure_windows_vm.py teardown --help
python3 scripts/azure_windows_vm.py dryrun
python3 scripts/azure_windows_vm.py teardown
python3 scripts/azure_windows_vm.py validate
```
