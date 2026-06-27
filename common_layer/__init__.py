"""
common_layer — shared foundation for every trading phase.

Sub-packages:
    configuration         typed plant/market/solver config from YAML
    physical_plant_models PSP / PV / BESS / reservoir / FCR physics + validation
    utilities             logging, market/plant timezones, calendar/ISP, audit
    database              committed positions, audit query, input schema validation
    gate_scheduler        resolve and fire daily market gates at CET times
"""
