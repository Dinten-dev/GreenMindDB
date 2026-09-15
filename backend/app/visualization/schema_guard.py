"""Keep separately managed visualization tables out of destructive autogeneration."""

TABLES = frozenset({"visual_chunk", "visual_reading", "visual_wav", "visual_wave", "visual_worker"})


def include_object(object_, name, type_, reflected, compare_to):
    return not (type_ == "table" and reflected and compare_to is None and name in TABLES)
