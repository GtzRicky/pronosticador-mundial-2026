param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath,
    [Parameter(Mandatory = $true)]
    [string]$ScriptPath,
    [string]$MonitorName = "DELL P2219H",
    [switch]$Hidden
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class ScheduledConsoleWindow
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
    public struct DISPLAY_DEVICE
    {
        public int cb;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
        public string DeviceName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
        public string DeviceString;
        public int StateFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
        public string DeviceID;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
        public string DeviceKey;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [DllImport("kernel32.dll")]
    public static extern IntPtr GetConsoleWindow();

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern bool EnumDisplayDevices(
        string lpDevice,
        uint iDevNum,
        ref DISPLAY_DEVICE lpDisplayDevice,
        uint dwFlags
    );

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    public static extern bool MoveWindow(
        IntPtr hWnd,
        int x,
        int y,
        int width,
        int height,
        bool repaint
    );

    public static bool DisplayContainsMonitor(string displayName, string monitorId)
    {
        for (uint index = 0; ; index++)
        {
            DISPLAY_DEVICE device = new DISPLAY_DEVICE();
            device.cb = Marshal.SizeOf(device);
            if (!EnumDisplayDevices(displayName, index, ref device, 0))
            {
                return false;
            }
            if (!String.IsNullOrEmpty(device.DeviceID) &&
                device.DeviceID.IndexOf(monitorId, StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return true;
            }
        }
    }
}
"@

function Convert-MonitorText {
    param([uint16[]]$Value)

    if (-not $Value) {
        return ""
    }
    $bytes = [byte[]]($Value | Where-Object { $_ -ne 0 })
    return [Text.Encoding]::ASCII.GetString($bytes)
}

function Move-ConsoleToMonitor {
    param([string]$FriendlyName)

    $wmiMonitor = Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID |
        Where-Object {
            $_.Active -and
            (Convert-MonitorText $_.UserFriendlyName) -eq $FriendlyName
        } |
        Select-Object -First 1
    if (-not $wmiMonitor) {
        Write-Warning "No se encontro el monitor '$FriendlyName'; se conserva la posicion predeterminada."
        return
    }

    $monitorId = ($wmiMonitor.InstanceName -split "\\")[1]
    $targetScreen = [System.Windows.Forms.Screen]::AllScreens |
        Where-Object {
            [ScheduledConsoleWindow]::DisplayContainsMonitor($_.DeviceName, $monitorId)
        } |
        Select-Object -First 1
    if (-not $targetScreen) {
        Write-Warning "No se pudo mapear '$FriendlyName' a una pantalla activa; se conserva la posicion predeterminada."
        return
    }

    $handle = [IntPtr]::Zero
    for ($attempt = 0; $attempt -lt 40 -and $handle -eq [IntPtr]::Zero; $attempt++) {
        $handle = [ScheduledConsoleWindow]::GetConsoleWindow()
        if ($handle -eq [IntPtr]::Zero) {
            Start-Sleep -Milliseconds 50
        }
    }
    if ($handle -eq [IntPtr]::Zero) {
        Write-Warning "No se encontro una ventana de consola para mover."
        return
    }

    $rect = New-Object ScheduledConsoleWindow+RECT
    if (-not [ScheduledConsoleWindow]::GetWindowRect($handle, [ref]$rect)) {
        Write-Warning "No se pudo leer la posicion de la consola."
        return
    }

    $workingArea = $targetScreen.WorkingArea
    $width = [Math]::Min([Math]::Max($rect.Right - $rect.Left, 640), $workingArea.Width)
    $height = [Math]::Min([Math]::Max($rect.Bottom - $rect.Top, 360), $workingArea.Height)
    $x = $workingArea.Left + [Math]::Max(0, [int](($workingArea.Width - $width) / 2))
    $y = $workingArea.Top + [Math]::Max(0, [int](($workingArea.Height - $height) / 2))
    [ScheduledConsoleWindow]::MoveWindow($handle, $x, $y, $width, $height, $true) | Out-Null
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "No se encontro Python: $PythonPath"
}
if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "No se encontro el script: $ScriptPath"
}

if (-not $Hidden) {
    Move-ConsoleToMonitor -FriendlyName $MonitorName
}
& $PythonPath $ScriptPath
exit $LASTEXITCODE
