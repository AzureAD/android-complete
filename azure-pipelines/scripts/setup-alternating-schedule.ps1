Param (
    [Parameter(Mandatory = $true)][String]$BuildingOnSchedule,
    [Parameter(Mandatory = $true)][String]$DayOfWeek,
    [Parameter(Mandatory = $true)][String]$FlightInput,
    [Parameter(Mandatory = $true)][String]$FlightSelection,
    [Parameter(Mandatory = $true)][String]$FlagInput,
    [Parameter(Mandatory = $true)][String]$BuildNumberInput,
    [Parameter(Mandatory = $false)][String]$FlightOutputVar="FlightOutput",
    [Parameter(Mandatory = $false)][String]$FlagOutputVar="FlagOutput",
    [Parameter(Mandatory = $false)][String]$LTWOutputVar="ShouldRunLtwTests"
)

#If this is a scheduled run, set up flight/flags variables based on day of the week
Write-Host "Running on Schedule? [$BuildingOnSchedule]"

# Trim any white space at the end of the flag input
$FlagInput = $FlagInput -replace "\s+$", ""

$LocalFlightTrueDays = "Monday","Wednesday","Friday"
$LocalFlightFalseDays = "Tuesday","Thursday"
$EcsFlightDays = "Saturday"
$FlightValue = ""
$FlagValue = ""
$ShouldRunLtwTests = "True"

# Check if this is a scheduled run
if ("$BuildingOnSchedule" -eq "True") {
    Write-Host "This run is scheduled on a ($DayOfWeek)"

    # Local Flights, set to true, pass flags days
    if ( $LocalFlightTrueDays.Contains("$DayOfWeek") ) {
        Write-Host "Scheduled: use local flights with true values, default flags passed"
        $FlightValue = $FlightInput -ireplace "false","true"
        $FlagValue = $FlagInput

        Write-Host "##vso[build.updatebuildnumber]$BuildNumberInput  [Scheduled $DayOfWeek, Local Flights Set to True, With Flags]"
    }

    # Local Flights, set to false, don't pass flags days
    elseif ( $LocalFlightFalseDays.Contains("$DayOfWeek") ) {
        Write-Host "Scheduled: use local flights with false values, no flags passed"
        $FlightValue = $FlightInput -ireplace "true","false"
        $FlagValue = ""
        $ShouldRunLtwTests = "False"

        Write-Host "##vso[build.updatebuildnumber]$BuildNumberInput  [Scheduled $DayOfWeek, Local Flights Set to False, No Flags]"
    }

    # ECS Flight Days
    elseif ( $EcsFlightDays.Contains("$DayOfWeek") ) {
        Write-Host "Scheduled: use ECS flights"
        $FlightValue = ""
        $FlagValue = ""
        $ShouldRunLtwTests = "False"

        Write-Host "##vso[build.updatebuildnumber]$BuildNumberInput  [Scheduled $DayOfWeek, ECS Flights, No Flags]"
    }

    # Default behavior if day does not have a defined behavior
    else {
        Write-Host "This day doesn't have a defined scheduled behavior"
        exit 1
    }
}

# Not a scheduled run
else {
    Write-Host "This is not a scheduled run, proceed with passed parameters."

    # If this is local flighting, should pass flights. If ECS, should pass no flights
    if ("$FlightSelection" -eq 'Local') {
      $FlightValue = "$FlightInput"
    } else {
      $FlightValue = ""
    }

    $FlagValue = "$FlagInput"
}

Write-Host "##vso[task.setvariable variable=$FlightOutputVar;isOutput=true]$FlightValue"
Write-Host "##vso[task.setvariable variable=$FlagOutputVar;isOutput=true]$FlagValue"
Write-Host "##vso[task.setvariable variable=$LTWOutputVar;isOutput=true]$ShouldRunLtwTests"
