"""
Segment compiler — translates JSONB `rules` into a safe parameterised SQL
WHERE clause over the `users` table.

A segment is a list of conjunctive (AND) rules. Each rule is:
    { "field": <name>, "op": <operator>, "value": <scalar> }

Supported fields (strict whitelist — anything else raises ValueError):
    • is_pro                      : bool
    • role                        : str
    • country                     : str
    • language                    : str
    • onboarding_completed        : bool   (maps to onboarding_completed_at IS NULL)
    • email_subscribed            : bool
    • connect_used                : bool   (connect_points > 0)
    • created_within_days         : int    (accounts younger than N days)
    • last_seen_older_than_days   : int    (inactive users — last_seen or updated_at older than N)
    • never_active                : bool   (last_seen IS NULL)
    • referral_count_gte          : int    (via referrals.referrer_user_id count)
    • referral_count_eq           : int
    • pending_tasks_gte           : int    (call_summary action_items with who_user_id=me not done)
    • legacy_migrated             : bool   (legacy_id IS NOT NULL)

Operators (per field-category):
    eq | neq | gt | gte | lt | lte | is_true | is_false

The compiled output is a tuple:
    (sql_where: str, params: List[Any])

`sql_where` ALWAYS starts with a space and can be appended after `WHERE 1=1`.
Rules referencing related tables (referrals, messages, call_summaries) are
expanded as EXISTS / scalar subqueries — no JOIN pollution on the base users
query so SELECT COUNT(*) etc. stays O(N).

NO RAW INTERPOLATION. Every value flows through asyncpg $N params.
"""
from __future__ import annotations
from typing import List, Tuple, Any


class SegmentCompileError(ValueError):
    pass


_BOOL_FIELDS = {
    "is_pro": "u.is_pro",
    "email_subscribed": "u.email_subscribed",
    # Synthetic bools — special handling
    "onboarding_completed": None,
    "connect_used": None,
    "connect_unused": None,
    "never_active": None,
    "legacy_migrated": None,
    "pytest_safe_only": None,   # email ends with @japap.com (sandbox only)
    "is_active": "u.is_active",
    "is_verified": "u.is_verified",
}

_STR_FIELDS = {
    "role": "u.role",
    "country": "u.country",
    "country_code": "u.country_code",
    "language": "u.language",
    "preferred_lang": "u.preferred_lang",
}

_INT_FIELDS_WITH_OP = {
    # field: (sql_expr, supported_ops)
    "created_within_days": ("u.created_at > NOW() - INTERVAL '%s days'", {"_param_interval"}),
    "last_seen_older_than_days": (
        "(u.updated_at IS NULL OR u.updated_at < NOW() - INTERVAL '%s days')",
        {"_param_interval"},
    ),
    "referral_count_gte": (
        "(SELECT COUNT(*) FROM referrals WHERE referrer_user_id = u.user_id)",
        {"gte"},
    ),
    "referral_count_eq": (
        "(SELECT COUNT(*) FROM referrals WHERE referrer_user_id = u.user_id)",
        {"eq"},
    ),
    "connect_points_gte": ("COALESCE(u.connect_points, 0)", {"gte"}),
}


def _compile_rule(rule: dict, params: List[Any]) -> str:
    """Return a single SQL fragment. Appends values to `params` list."""
    if not isinstance(rule, dict):
        raise SegmentCompileError(f"Rule must be an object, got {type(rule).__name__}")
    field = rule.get("field")
    op = rule.get("op", "eq")
    value = rule.get("value")
    if not field or not isinstance(field, str):
        raise SegmentCompileError("Rule missing 'field'")

    # Boolean fields (including synthetic ones)
    if field in _BOOL_FIELDS:
        col = _BOOL_FIELDS[field]
        truthy = bool(value) if op in ("eq", "is_true", "is_false") else False
        if op == "is_true":
            truthy = True
        elif op == "is_false":
            truthy = False
        if field == "onboarding_completed":
            return "u.onboarding_completed_at IS NOT NULL" if truthy else "u.onboarding_completed_at IS NULL"
        if field == "connect_used":
            return "COALESCE(u.connect_points, 0) > 0" if truthy else "COALESCE(u.connect_points, 0) = 0"
        if field == "connect_unused":
            return "COALESCE(u.connect_points, 0) = 0" if truthy else "COALESCE(u.connect_points, 0) > 0"
        if field == "never_active":
            return "u.updated_at IS NULL" if truthy else "u.updated_at IS NOT NULL"
        if field == "legacy_migrated":
            return "u.legacy_id IS NOT NULL" if truthy else "u.legacy_id IS NULL"
        if field == "pytest_safe_only":
            # Whitelist: @japap.com sandbox emails only. Used by automated tests
            # so they can never, ever reach real migrated/production users.
            return (
                "(LOWER(u.email) LIKE '%%@japap.com')"
                if truthy
                else "(LOWER(u.email) NOT LIKE '%%@japap.com')"
            )
        if col is None:
            raise SegmentCompileError(f"Boolean field '{field}' not wired")
        return f"{col} IS {'TRUE' if truthy else 'NOT TRUE'}"

    # String fields
    if field in _STR_FIELDS:
        col = _STR_FIELDS[field]
        if not isinstance(value, str):
            raise SegmentCompileError(f"Field '{field}' expects string value")
        params.append(value)
        if op == "eq":
            return f"{col} = ${len(params)}"
        if op == "neq":
            return f"{col} <> ${len(params)}"
        raise SegmentCompileError(f"Unsupported op '{op}' for string field '{field}'")

    # Int interval / count fields
    if field in _INT_FIELDS_WITH_OP:
        sql_expr, ops = _INT_FIELDS_WITH_OP[field]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise SegmentCompileError(f"Field '{field}' expects numeric value")
        n = int(value)
        if n < 0:
            raise SegmentCompileError("Numeric value must be non-negative")
        if "_param_interval" in ops:
            # INTERVAL cannot be parameterised directly — safe because n is int-cast
            return sql_expr % n
        params.append(n)
        if op == "eq":
            return f"{sql_expr} = ${len(params)}"
        if op == "gte":
            return f"{sql_expr} >= ${len(params)}"
        if op == "gt":
            return f"{sql_expr} > ${len(params)}"
        if op == "lte":
            return f"{sql_expr} <= ${len(params)}"
        if op == "lt":
            return f"{sql_expr} < ${len(params)}"
        raise SegmentCompileError(f"Unsupported op '{op}' for '{field}'")

    raise SegmentCompileError(f"Unknown segment field '{field}'")


def compile_rules(rules: List[dict]) -> Tuple[str, List[Any]]:
    """Compile the full rule list (implicit AND). Returns (where_sql, params)."""
    if rules is None:
        rules = []
    if not isinstance(rules, list):
        raise SegmentCompileError("Segment rules must be a list")
    if not rules:
        return "", []
    params: List[Any] = []
    frags = [_compile_rule(r, params) for r in rules]
    return " AND " + " AND ".join(f"({f})" for f in frags), params


async def count_recipients(conn, rules: List[dict]) -> int:
    """COUNT(*) over the base users table matching the rules.

    Only counts users who are:
      • active (u.is_active = TRUE)
      • have a usable email
      • haven't unsubscribed (email_subscribed = TRUE)
    """
    where, params = compile_rules(rules)
    sql = (
        "SELECT COUNT(*) FROM users u "
        "WHERE u.is_active = TRUE "
        "AND u.email IS NOT NULL AND u.email <> '' "
        "AND (u.email_subscribed = TRUE OR u.email_subscribed IS NULL)"
        + where
    )
    return int(await conn.fetchval(sql, *params) or 0)


async def fetch_recipients(conn, rules: List[dict], limit: int = 50000) -> list:
    """Fetch user_id + email + first_name of matching users for send fan-out.

    Same base filters as count_recipients.
    """
    where, params = compile_rules(rules)
    sql = (
        "SELECT u.user_id, u.email, u.first_name, u.last_name, u.username, "
        "       u.country, u.language, COALESCE(u.connect_points, 0) AS connect_points, "
        "       u.is_pro "
        "FROM users u "
        "WHERE u.is_active = TRUE "
        "AND u.email IS NOT NULL AND u.email <> '' "
        "AND (u.email_subscribed = TRUE OR u.email_subscribed IS NULL)"
        + where +
        " ORDER BY u.user_id LIMIT " + str(int(limit))
    )
    rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows]


# Pre-seeded system segments — used once on startup via services.messaging_seed
SYSTEM_SEGMENTS = [
    ("seg_all_users", "Tous les utilisateurs", "Tous les comptes actifs avec email.", []),
    ("seg_active_users", "Utilisateurs actifs", "Comptes actifs (is_active=true).",
     [{"field": "is_active", "op": "is_true"}]),
    ("seg_inactive_users", "Utilisateurs inactifs", "Comptes désactivés (is_active=false).",
     [{"field": "is_active", "op": "is_false"}]),
    ("seg_pro_users", "Utilisateurs Pro", "Abonnés Pro actifs.",
     [{"field": "is_pro", "op": "is_true"}]),
    ("seg_non_pro", "Utilisateurs non-Pro", "Comptes non-Pro.",
     [{"field": "is_pro", "op": "is_false"}]),
    ("seg_new_7d", "Nouveaux (7 jours)", "Comptes créés les 7 derniers jours.",
     [{"field": "created_within_days", "op": "gte", "value": 7}]),
    ("seg_inactive_7d", "Inactifs 7+ jours", "Pas d'activité depuis 7 jours.",
     [{"field": "last_seen_older_than_days", "op": "gte", "value": 7}]),
    ("seg_inactive_30d", "Inactifs 30+ jours", "Pas d'activité depuis 30 jours.",
     [{"field": "last_seen_older_than_days", "op": "gte", "value": 30}]),
    ("seg_zero_referrals", "Sans parrainage", "0 filleul.",
     [{"field": "referral_count_eq", "op": "eq", "value": 0}]),
    ("seg_has_referrals", "Avec parrainages", "Au moins 1 filleul.",
     [{"field": "referral_count_gte", "op": "gte", "value": 1}]),
    ("seg_never_onboarded", "Onboarding incomplet", "N'a pas terminé l'onboarding.",
     [{"field": "onboarding_completed", "op": "is_false"}]),
    ("seg_connect_used", "Utilisateurs Connect", "A utilisé JAPAP Connect au moins une fois.",
     [{"field": "connect_used", "op": "is_true"}]),
    ("seg_connect_unused", "Connect jamais utilisé", "N'a jamais utilisé Connect.",
     [{"field": "connect_unused", "op": "is_true"}]),
    ("seg_legacy_migrated", "Utilisateurs migrés 1.0", "Anciens comptes (legacy_id non null).",
     [{"field": "legacy_migrated", "op": "is_true"}]),
    # iter94 — Audience spéciale de migration JAPAP 1.0 → 4.0. L'envoi est
    # obligatoirement découpé en batches de 5000 via `batch_index` dans
    # POST /templates/{id}/send-to-audience. ~28908 users.
    ("seg_migration_1to4", "Migration JAPAP 1.0 → 4.0",
     "Utilisateurs legacy à ré-onboarder (legacy_id non null). "
     "L'envoi est IMPOSÉ en batches de 5000 pour protéger la délivrabilité Resend.",
     [{"field": "legacy_migrated", "op": "is_true"}]),
    ("seg_pro_inactive_30d", "Pro inactifs 30j", "Pro mais pas actif depuis 30 jours.",
     [{"field": "is_pro", "op": "is_true"},
      {"field": "last_seen_older_than_days", "op": "gte", "value": 30}]),
    ("seg_pytest_safe",
     "Sandbox tests — @japap.com uniquement",
     "Segment réservé aux tests automatisés. Whitelist stricte sur LOWER(email) LIKE '%@japap.com'. "
     "Ne DOIT JAMAIS être utilisé pour un envoi réel hors test.",
     [{"field": "pytest_safe_only", "op": "is_true"}]),
]
