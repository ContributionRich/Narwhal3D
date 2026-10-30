"""Presentation of trusted native CAD evidence, never inferred from provider prose."""


def status_text(state):
    if state is None:  # older ABI1 libraries do not expose this additive result
        return None
    if not isinstance(state, dict):
        return "CAD evidence unavailable."
    known = state.get("known_action_results")
    created = state.get("approved_creation_receipts")
    if (type(known) is not int or type(created) is not int or not 0 <= created <= known <= 8
            or state.get("provider_text_is_geometry_evidence") is not False
            or state.get("current_geometry_verified") is not False):
        return "CAD evidence unavailable."
    if created:
        return (f"CAD evidence: {created} approved creation receipt(s). "
                "Current geometry has not been independently reverified.")
    return "CAD evidence: no approved geometry creation in this turn. Provider text is not execution evidence."
