param([switch]$Visible)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class NativeShow
{
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
'@

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Computer Use MCP Lab Target'
$form.Size = New-Object System.Drawing.Size(520, 260)
$form.StartPosition = 'Manual'
# Deliberately place the test app off-screen and keep it out of the taskbar.
$form.Location = New-Object System.Drawing.Point(-10000, -10000)
$form.ShowInTaskbar = $false

$label = New-Object System.Windows.Forms.Label
$label.Text = 'Computer Use MCP Lab'
$label.AutoSize = $true
$label.Location = New-Object System.Drawing.Point(30, 30)
$form.Controls.Add($label)

$entry = New-Object System.Windows.Forms.TextBox
$entry.Name = 'entry'
$entry.AccessibleName = 'Entry'
$entry.Location = New-Object System.Drawing.Point(30, 75)
$entry.Size = New-Object System.Drawing.Size(320, 28)
$form.Controls.Add($entry)

$button = New-Object System.Windows.Forms.Button
$button.Name = 'applyButton'
$button.AccessibleName = 'Apply'
$button.Text = 'Apply'
$button.Location = New-Object System.Drawing.Point(365, 73)
$button.Size = New-Object System.Drawing.Size(100, 30)
$form.Controls.Add($button)

$result = New-Object System.Windows.Forms.Label
$result.Name = 'result'
$result.AccessibleName = 'Result'
$result.Text = 'waiting'
$result.AutoSize = $true
$result.Location = New-Object System.Drawing.Point(30, 125)
$form.Controls.Add($result)

$check = New-Object System.Windows.Forms.CheckBox
$check.Name = 'check'; $check.Text='Enable test'; $check.AccessibleName='Enable test'
$check.Location=[System.Drawing.Point]::new(30,160)
$form.Controls.Add($check)
$slider = New-Object System.Windows.Forms.TrackBar
$slider.Name='slider'; $slider.AccessibleName='Level'; $slider.Maximum=100
$slider.Location=[System.Drawing.Point]::new(200,150); $slider.Size=[System.Drawing.Size]::new(250,40)
$form.Controls.Add($slider)
if($Visible) {
    $area=[System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
    $form.Location=[System.Drawing.Point]::new($area.Left+80,$area.Top+80)
}

$button.Add_Click({
    $result.Text = 'Hello, ' + $entry.Text
    $form.Text = 'Computer Use MCP Lab Target - ' + $result.Text
})

# Force handle creation while the form is still hidden, then show it without
# activation. SW_SHOWNOACTIVATE = 4. This avoids stealing the user's foreground.
$form.CreateControl()
[void][NativeShow]::ShowWindow($form.Handle, 4)
[System.Windows.Forms.Application]::Run()
