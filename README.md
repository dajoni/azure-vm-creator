# Azure VM Utility

This workspace contains:

- `scripts/azure_windows_vm.py`
- `scripts/configure_windows_vm.ps1`
- `scripts/configure_linux_vm.sh`

It creates an idempotent Windows Server or Linux VM in the current Azure CLI default subscription. The VM has a public IP address for outbound internet access, but the script does not create a public inbound RDP or SSH rule. Access is intended to go through Azure Bastion Developer in the Azure portal.

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
python3 scripts/azure_windows_vm.py apply --os-type linux
```

Create or complete missing resources, then run guest configuration:

```bash
python3 scripts/azure_windows_vm.py apply --configure
python3 scripts/azure_windows_vm.py apply --os-type linux --configure
```

Validate the deployed Azure state against the script's expected configuration:

```bash
python3 scripts/azure_windows_vm.py validate
python3 scripts/azure_windows_vm.py validate --os-type linux
```

Run guest configuration against an existing VM:

```bash
python3 scripts/azure_windows_vm.py configure
python3 scripts/azure_windows_vm.py configure --os-type linux
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
python3 scripts/azure_windows_vm.py apply --os-type linux
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

Linux examples:

```bash
python3 scripts/azure_windows_vm.py dryrun --os-type linux
python3 scripts/azure_windows_vm.py apply --os-type linux
python3 scripts/azure_windows_vm.py apply --os-type linux --ssh-key-values ~/.ssh/id_ed25519.pub
python3 scripts/azure_windows_vm.py apply --os-type linux --linux-image Ubuntu2404
python3 scripts/azure_windows_vm.py configure --os-type linux
python3 scripts/azure_windows_vm.py nsg ssh enable --os-type linux
python3 scripts/azure_windows_vm.py nsg ssh disable --os-type linux
python3 scripts/azure_windows_vm.py nsg rdp enable --os-type linux
python3 scripts/azure_windows_vm.py nsg rdp disable --os-type linux
python3 scripts/azure_windows_vm.py validate --os-type linux
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

`--image-sku` applies to Windows only. Linux uses `--linux-image`, which accepts an Azure CLI image alias, full URN, custom image name, or image ID. The Linux default is Canonical Ubuntu 26.04 LTS:

```text
Canonical:ubuntu-26_04-lts:server:latest
```

Guest configuration options are available on `apply`, `configure`, and `recreate`:

```bash
python3 scripts/azure_windows_vm.py configure --config-script scripts/configure_windows_vm.ps1
python3 scripts/azure_windows_vm.py configure --os-type linux --config-script scripts/configure_linux_vm.sh
python3 scripts/azure_windows_vm.py configure --run-command-name configure-windows-vm
```

For Windows, `--config-script` points to the local PowerShell staging script. The staging script is copied through Azure Run Command, writes the desktop installer to `C:\ProgramData\AzureVmCreator\configure_windows_vm.ps1`, and registers a one-time scheduled task for the next interactive `azureuser` logon.

For Linux, `--config-script` points to `scripts/configure_linux_vm.sh` by default. The script runs through Azure `RunShellScript`, prompts locally for the Linux desktop password, installs Docker from Docker's official Ubuntu apt repository, installs agent-host tools, installs a lightweight XFCE/xrdp desktop, installs Google Chrome from Google's Debian package, installs Firefox, installs Claude Desktop from Anthropic's apt repository after verifying its signing key fingerprint, installs ChatGPT Desktop for Linux from OpenAI's Linux package, creates desktop launchers for Chrome, Firefox, ChatGPT, and Claude, starts Docker and xrdp, sets the `azureuser` desktop password, and resets/configures UFW inside the guest to allow only OpenSSH and TCP `3389` inbound. It does not create, update, or delete Azure NSG rules.

`--run-command-name` is used as the base name for a temporary managed Run Command resource. The script appends a timestamp and random suffix for each staging run.

Docker/UFW caveat: Docker-published ports can bypass UFW unless Docker networking is controlled separately.

## Linux NSG Toggles

Linux VM creation does not create public inbound SSH or RDP rules. Linux guest configuration also does not change Azure NSG rules. Use Azure Bastion Developer for portal SSH by default, and use explicit `nsg` commands only when direct public access is needed.

To enable direct public SSH on demand:

```bash
python3 scripts/azure_windows_vm.py nsg ssh enable --os-type linux
```

To disable the script-managed direct public SSH rule:

```bash
python3 scripts/azure_windows_vm.py nsg ssh disable --os-type linux
```

To enable public xrdp access after `configure --os-type linux`:

```bash
python3 scripts/azure_windows_vm.py nsg rdp enable --os-type linux
```

To disable the script-managed public xrdp rule:

```bash
python3 scripts/azure_windows_vm.py nsg rdp disable --os-type linux
```

The toggles manage only script-owned rules. SSH uses `AllowSshFromInternet` on TCP `22` with priority `1000`; RDP uses `AllowLinuxRdpFromInternet` on TCP `3389` with priority `1010`. Disabling removes only the selected managed rule and does not delete user-created rules with other names.

## Defaults

- Region: `northeurope`
- OS type: `windows`
- Windows resource group: `rg-secure-winvm`
- Linux resource group: `rg-secure-linuxvm`
- Windows VM name: `vm-secure-win`
- Linux VM name: `vm-secure-linux`
- Windows image: `MicrosoftWindowsServer:WindowsServer:2025-datacenter-azure-edition:latest`
- Windows image SKU: `2025-datacenter-azure-edition`
- Linux image: `Canonical:ubuntu-26_04-lts:server:latest`
- Size: `Standard_B2as_v2` with 2 vCPU and 8 GiB RAM
- OS disk: `127` GB
- OS disk storage: `StandardSSD_LRS`
- OS disk delete option: `Delete`
- Windows VNet: `vnet-secure-win`
- Linux VNet: `vnet-secure-linux`
- Windows subnet: `subnet-secure-win`
- Linux subnet: `subnet-secure-linux`
- Address space: `10.42.0.0/16`
- VM subnet prefix: `10.42.1.0/24`
- Bastion: Developer SKU
- Windows VM public IP: Standard static public IP named `pip-secure-win`
- Linux VM public IP: Standard static public IP named `pip-secure-linux`
- Outbound internet: VM public IP
- Default public RDP/SSH NSG rule: none
- Linux direct public SSH/RDP rules: disabled by default; toggle with `nsg ssh|rdp`
- Windows guest configuration staging script: `scripts/configure_windows_vm.ps1`
- Linux guest configuration script: `scripts/configure_linux_vm.sh`
- Windows managed Run Command base name: `configure-windows-vm`
- Linux Run Command base name: `configure-linux-vm`

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

Bastion Developer supports portal-based browser RDP for Windows and portal-based browser SSH for Linux. The script does not create a local tunnel or native client connection.

The public IP is present so the VM can initiate outbound internet connections. The script still passes `--nsg-rule NONE` to `az vm create`, so Azure CLI does not add a public inbound RDP or SSH rule. For Linux, use `nsg ssh enable --os-type linux` or `nsg rdp enable --os-type linux` only when you intentionally want direct public access.

Use `validate` to check that the deployed resources still match the script, including the VM size, disk settings, image publisher/offer/SKU when the expected image is a full URN, expected public IP attachment, Bastion SKU, and VNet/subnet prefixes. Windows validation fails on public inbound RDP. Linux validation warns, but does not fail, when public inbound SSH or RDP is enabled. Validation also prints all configured custom NSG rules plus the VM private and public IP addresses when the VM can be read.

Use `configure`, `apply --configure`, or `recreate --execute --configure` to stage guest configuration through a temporary managed Azure VM Run Command resource. This does not require public inbound RDP or WinRM. Azure Run Command runs only the staging step in the default managed context, prints the staging output, then deletes the temporary managed Run Command resource.

The staged task is named `AzureVmCreator-ConfigureDesktop`. It runs at the next interactive logon for the configured admin user and starts a visible PowerShell window with:

```powershell
powershell.exe -NoExit -ExecutionPolicy Bypass -File C:\ProgramData\AzureVmCreator\configure_windows_vm.ps1
```

The desktop installer writes `C:\ProgramData\AzureVmCreator\configure.log`, installs ChatGPT, Claude, and Git through `winget`, writes `C:\ProgramData\AzureVmCreator\configured.txt` after success, and removes the scheduled task only after all installs succeed. Failed installs leave the task in place for retry at the next login.

Linux configuration runs immediately through `RunShellScript`. It installs `git`, `tmux`, `ufw`, Docker, common build and agent-host tools, `xubuntu-desktop-minimal` with xrdp, Google Chrome, Firefox, Claude Desktop, and ChatGPT Desktop for Linux. It creates desktop launchers for Chrome, Firefox, ChatGPT, and Claude. Google Chrome is installed on `amd64`; the script skips Chrome on other architectures if Google's Debian package is unavailable. After configuration succeeds, enable public RDP explicitly with:

```bash
python3 scripts/azure_windows_vm.py nsg rdp enable --os-type linux
```

## Password Handling

The script prompts for the local Windows admin password only when it needs to create a Windows VM. Windows guest configuration staging does not require or store the Windows password. It registers an interactive logon task for the configured admin user instead.

Linux `configure --os-type linux` prompts for the Linux desktop password and passes it once to Azure Run Command so xrdp can authenticate the configured admin user. The script redacts that parameter in local command logging.

One caveat: Azure CLI receives passwords as process arguments during Windows VM creation and Linux guest configuration. Avoid running those commands from a shared machine where local process arguments may be visible to other users.

## Linux SSH Keys

Linux VM creation uses SSH authentication. If `--ssh-key-values` is omitted, the script passes `--generate-ssh-keys` to `az vm create`. Azure CLI creates SSH public and private key files only if they are missing and stores them in `~/.ssh`; it does not intentionally rotate existing keys.

Use `--ssh-key-values` to provide one or more public key file paths or public key values. When `--ssh-key-values` is provided, the script does not pass `--generate-ssh-keys`.

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
bash -n scripts/configure_linux_vm.sh
python3 scripts/azure_windows_vm.py --help
python3 scripts/azure_windows_vm.py dryrun --help
python3 scripts/azure_windows_vm.py apply --help
python3 scripts/azure_windows_vm.py validate --help
python3 scripts/azure_windows_vm.py configure --help
python3 scripts/azure_windows_vm.py recreate --help
python3 scripts/azure_windows_vm.py teardown --help
python3 scripts/azure_windows_vm.py nsg --help
python3 scripts/azure_windows_vm.py dryrun
python3 scripts/azure_windows_vm.py dryrun --os-type linux
python3 scripts/azure_windows_vm.py teardown
python3 scripts/azure_windows_vm.py validate
```
