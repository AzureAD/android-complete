Param (
    [Parameter(Mandatory = $false)][String]$OutputVariable="DayOfWeek"
)

$WeekDay = ( get-date ).DayOfWeek.value__

Write-Host "Setting  $OutputVariable"
Write-Host "$BuildNumberOutputOnSuccessVar = $WeekDay"
Write-Host "##vso[task.setvariable variable=$($OutputVariable)]$WeekDay"
