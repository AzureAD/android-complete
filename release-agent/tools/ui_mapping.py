"""Explicit tested combinations, not MSAL substring aliases. No provider I/O."""
import re

# The variant is the complete MSAL/Broker pair. BrokerHost intentionally rolls up
# into RC Broker, matching the master's LocalBrokerHostDebug configurations.
UI_CONFIG_FLIGHT_VARIANT = {
    292: ("ECS", "prod_msal_rc_broker"),
    294: ("ECS", "rc_msal_prod_broker"),
    293: ("ECS", "rc_msal_rc_broker"),
    328: ("Local", "prod_msal_rc_broker"),
    344: ("Local", "rc_msal_prod_broker"),
    330: ("Local", "rc_msal_rc_broker"),
}
CONFIG_NAMES = {
    292: "PROD MSAL - RC Broker (ECS)", 294: "RC MSAL - PROD Broker (ECS)",
    293: "RC MSAL - RC Broker", 328: "PROD MSAL - RC Broker (LocalFlights)",
    344: "RC MSAL - PROD Broker (LocalFlight)", 330: "RC MSAL - RC Broker (LocalFlights)",
}


def config_for(flight, variant):
    return next(cid for cid, pair in UI_CONFIG_FLIGHT_VARIANT.items() if pair == (flight, variant))


def route_suite(name):
    base = re.sub(r"\s*\(API\s*\d+\)\s*$", "", name, flags=re.I).strip()
    routes = {
        "PROD MSAL - RC Broker": ("prod_msal_rc_broker", "exact_combination"),
        "RC MSAL - PROD Broker": ("rc_msal_prod_broker", "exact_combination"),
        "RC MSAL - RC Broker": ("rc_msal_rc_broker", "exact_combination"),
        "LTW, RC MSAL - RC Broker": ("rc_msal_rc_broker", "ltw_exact_combination"),
        "Stress Tests - RC MSAL with RC Broker": ("rc_msal_rc_broker", "stress_exact_combination"),
        "PROD MSAL - RC BrokerHost": ("prod_msal_rc_broker", "brokerhost_explicit_rollup"),
    }
    return routes.get(base, (None, "unknown_suite_variant"))
