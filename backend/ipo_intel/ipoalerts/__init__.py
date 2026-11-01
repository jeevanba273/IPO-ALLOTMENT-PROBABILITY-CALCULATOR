"""ipoalerts (https://api.ipoalerts.in): grey market premium history only.

Only GET /ipos/{symbol}/gmp is used. The IPO list, IPO detail and includeGmp
are paid features (400 ERR:PRMFBNGMP), and every call spends a scarce daily
budget, so nothing else is ever requested.
"""
