// Keep the wire data numeric; formatting belongs to the view.
function parseSnapshot(text) {
    var data;
    try {
        data = JSON.parse(text);
    } catch (e) {
        throw new Error("au returned invalid JSON.");
    }
    if (!data || typeof data.generated_at !== "string"
            || !isFinite(Date.parse(data.generated_at)) || !Array.isArray(data.rows))
        throw new Error("au returned an invalid snapshot.");

    for (var i = 0; i < data.rows.length; i++) {
        var row = data.rows[i];
        if (!row || typeof row.provider !== "string" || !row.provider
                || typeof row.label !== "string" || !row.label)
            throw new Error("au returned an invalid quota row.");
        if (typeof row.error === "string" && row.error.length > 0) {
            if (row.used_percent !== null || row.reset_at !== null || row.window_seconds !== null)
                throw new Error("au returned an invalid error row.");
        } else if (row.error !== null || typeof row.used_percent !== "number"
                || !isFinite(row.used_percent) || row.used_percent < 0
                || typeof row.window_seconds !== "number" || !isFinite(row.window_seconds)
                || row.window_seconds <= 0 || typeof row.reset_at !== "string"
                || !isFinite(Date.parse(row.reset_at))) {
            throw new Error("au returned an invalid quota row.");
        }
    }
    return data;
}

function duration(seconds) {
    var minutes = Math.max(1, Math.ceil(seconds / 60));
    var days = Math.floor(minutes / 1440);
    var hours = Math.floor(minutes / 60) % 24;
    if (days > 0) return days + "d" + (hours ? " " + hours + "h" : "");
    if (hours > 0) return hours + "h" + (minutes % 60 ? " " + minutes % 60 + "m" : "");
    return minutes + "m";
}

function countdown(resetAt, nowMs) {
    var seconds = (Date.parse(resetAt) - nowMs) / 1000;
    return seconds <= 0 ? "now" : duration(seconds);
}

function usagePace(row, generatedAt, nowMs) {
    var unknown = {level: "normal", hint: "Pace unavailable. Refresh quota data."};
    if (!row || row.error !== null || typeof row.used_percent !== "number"
            || !isFinite(row.used_percent) || row.used_percent < 0
            || typeof row.window_seconds !== "number" || !isFinite(row.window_seconds)
            || row.window_seconds <= 0)
        return unknown;

    var observedMs = Date.parse(generatedAt);
    var resetMs = Date.parse(row.reset_at);
    var windowMs = row.window_seconds * 1000;
    var elapsedMs = observedMs - (resetMs - windowMs);
    if (!isFinite(observedMs) || !isFinite(resetMs) || !isFinite(nowMs)
            || observedMs > nowMs || elapsedMs < 0)
        return unknown;
    if (nowMs >= resetMs)
        return {level: "normal", hint: "This reset has passed. Refresh quota data."};
    if (row.used_percent >= 100)
        return {level: "red", hint: "Quota exhausted at the last refresh."};
    if (row.used_percent === 0)
        return {level: "normal", hint: "No quota used at the last refresh."};
    if (elapsedMs === 0)
        return unknown;

    // Use the observation time, not the ticking clock: waiting without new
    // usage data must not make a cached reading look like a slower burn rate.
    var fraction = (elapsedMs / windowMs) / (row.used_percent / 100);
    var level = fraction <= 0.25 ? "red"
        : fraction <= 0.5 ? "orange"
        : fraction <= 0.75 ? "yellow" : "normal";
    var hint = fraction < 1
        ? "Projected to run out " + (fraction * 100).toFixed(1) + "% into this window."
        : "Projected to last until reset.";
    return {level: level, hint: hint};
}
