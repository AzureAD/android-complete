Param (
    [Parameter(Mandatory = $false)][String]$OutputVariable="DayOfWeek"
)

$WeekDay = ( get-date ).DayOfWeek

Write-Host "Setting $OutputVariable"
Write-Host "Day of the week = $WeekDay"
Write-Host "##vso[task.setvariable variable=$($OutputVariable)]Thursday"
