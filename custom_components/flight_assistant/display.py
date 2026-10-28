"""Hours-and-minutes durations, shown on the form confirmation screen."""
def minutes_text(minutes):
    n = round(float(minutes))
    if n < 0:
        raise ValueError("Duration must be positive")
    hours, rest = divmod(n, 60)
    if hours and rest:
        return f"{hours} t {rest} min"
    if hours:
        return f"{hours} t"
    return f"{rest} min"
