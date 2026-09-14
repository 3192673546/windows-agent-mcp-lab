param([switch]$Cover)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class CaptureFixtureNative {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
}
'@
$form=[System.Windows.Forms.Form]::new()
$form.StartPosition='Manual'
$form.ShowInTaskbar=$false
if($Cover) {
    $form.Text='Computer Use MCP Lab Target Capture Cover'
    $form.Location=[System.Drawing.Point]::new(250,200)
    $form.Size=[System.Drawing.Size]::new(220,160)
    $form.BackColor=[System.Drawing.Color]::Lime
    $form.TopMost=$true
} else {
    $form.Text='Computer Use MCP Lab Target Capture'
    $form.Location=[System.Drawing.Point]::new(120,100)
    $form.Size=[System.Drawing.Size]::new(600,360)
    $form.BackColor=[System.Drawing.Color]::Magenta
}
$form.CreateControl()
[void][CaptureFixtureNative]::ShowWindow($form.Handle,4)
[System.Windows.Forms.Application]::Run()
