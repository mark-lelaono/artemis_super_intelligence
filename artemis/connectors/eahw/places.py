"""Anchor points across the eight IGAD member states.

Deliberately a short fixed list rather than a geocoder. A silently
mis-geocoded town yields a number that looks entirely plausible and is wrong,
which is the failure mode this project exists to avoid. Anything not listed
here needs explicit coordinates from the caller.

All points lie inside the service's coverage domain (21-51E, -11.75-24N); the
test suite asserts it.
"""

KNOWN_PLACES: dict[str, tuple[float, float]] = {
    # Kenya
    "nairobi": (36.8219, -1.2921),
    "mombasa": (39.6682, -4.0435),
    "kisumu": (34.7617, -0.0917),
    "turkana": (35.5966, 3.1167),
    "marsabit": (37.9899, 2.3284),
    "garissa": (39.6583, -0.4532),
    # Ethiopia
    "addis ababa": (38.7578, 8.9806),
    "dire dawa": (41.8661, 9.5931),
    "mekelle": (39.4753, 13.4967),
    "gode": (43.5500, 5.9527),
    # Somalia
    "mogadishu": (45.3182, 2.0469),
    "baidoa": (43.6486, 3.1246),
    "hargeisa": (44.0650, 9.5600),
    "kismayo": (42.5453, -0.3582),
    # South Sudan
    "juba": (31.5825, 4.8594),
    "malakal": (31.6605, 9.5334),
    # Sudan
    "khartoum": (32.5599, 15.5007),
    "el fasher": (25.3494, 13.6283),
    # Uganda
    "kampala": (32.5825, 0.3476),
    "gulu": (32.2881, 2.7746),
    # Djibouti / Eritrea
    "djibouti": (43.1456, 11.5721),
    "asmara": (38.9251, 15.3229),
}
