$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
[xml]$xaml=@'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
 Title="Computer Use MCP Lab Target WPF" Width="480" Height="380"
 Left="-10000" Top="-10000" ShowActivated="False" ShowInTaskbar="False" WindowStartupLocation="Manual">
 <StackPanel>
  <TextBox Name="entry" AutomationProperties.AutomationId="entry" AutomationProperties.Name="WPF entry" Text="initial"/>
  <Button Name="button" AutomationProperties.AutomationId="button" Content="Apply WPF"/>
  <CheckBox Name="check" AutomationProperties.AutomationId="check" Content="Enable WPF"/>
  <Slider Name="slider" AutomationProperties.AutomationId="slider" Minimum="0" Maximum="100"/>
  <Expander Name="expander" AutomationProperties.AutomationId="expander" Header="Details"><TextBlock Text="Expanded content"/></Expander>
  <ListBox Name="list" AutomationProperties.AutomationId="list">
   <ListBoxItem AutomationProperties.AutomationId="choiceA">A</ListBoxItem>
   <ListBoxItem AutomationProperties.AutomationId="choiceB">B</ListBoxItem>
  </ListBox>
 </StackPanel>
</Window>
'@
$window=[Windows.Markup.XamlReader]::Load([System.Xml.XmlNodeReader]::new($xaml))
$window.FindName('button').Add_Click({$window.Title='Computer Use MCP Lab Target WPF - '+$window.FindName('entry').Text})
$window.Add_ContentRendered({$window.Title='Computer Use MCP Lab Target WPF Ready'})
$app=[Windows.Application]::new()
[void]$app.Run($window)
