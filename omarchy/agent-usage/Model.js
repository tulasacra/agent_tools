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
                || (row.window_seconds !== null && (typeof row.window_seconds !== "number"
                    || !isFinite(row.window_seconds) || row.window_seconds <= 0))
                || typeof row.reset_at !== "string"
                || !isFinite(Date.parse(row.reset_at))) {
            throw new Error("au returned an invalid quota row.");
        }
        // Older snapshots and malformed optional balances keep valid quotas usable.
        var count = row.manual_resets_available;
        row.manual_resets_available = typeof count === "number" && isFinite(count)
            && count >= 0 && Math.floor(count) === count ? count : null;
        var expirations = row.manual_reset_expirations;
        row.manual_reset_expirations = Array.isArray(expirations) ? expirations.map(function(value) {
            if (typeof value !== "string"
                    || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value))
                return null;
            // Qt rounds extra fractional digits; truncate them like JS Dates
            // so a near-minute expiry cannot round into the following minute.
            value = value.replace(/(\.\d{3})\d+/, "$1");
            return isFinite(Date.parse(value)) ? new Date(value).toISOString() : null;
        }).sort(function(a, b) {
            if (a === null) return b === null ? 0 : 1;
            if (b === null) return -1;
            return Date.parse(a) - Date.parse(b);
        }) : null;
    }
    return data;
}

function manualResetSummary(rows, index, nowMs, formatDate) {
    var row = rows[index];
    if (!row || row.provider !== "codex") return null;
    for (var next = index + 1; next < rows.length; next++) {
        if (rows[next].provider === "codex") return null;
    }
    var count = row.manual_resets_available;
    var summary = "Manual resets available: " + (count == null ? "—" : count);
    if (count === 0) return {label: summary, remaining: "", date: ""};
    var dates = row.manual_reset_expirations;
    var first = dates && dates.length > 0 ? dates[0] : null;
    if (first === null) return {label: summary, remaining: "—", date: "—"};
    var complete = count != null && dates.length === count && dates.indexOf(null) < 0;
    var passed = Date.parse(first) <= nowMs;
    return {
        label: summary,
        remaining: countdown(first, nowMs),
        date: formatDate(first, false) + (complete ? "" : " (earliest known)")
            + (passed ? " (passed; refresh)" : "")
    };
}

function manualResetTooltip(row, nowMs, formatDate) {
    if (row.provider !== "codex" || row.manual_resets_available === 0) return "";
    var dates = row.manual_reset_expirations;
    if (dates == null) return "Expiration details unavailable.";
    var lines = ["Reset expirations (local time)"];
    for (var i = 0; i < dates.length; i++) {
        lines.push(dates[i] === null ? "Expiration unknown"
            : formatDate(dates[i], true) + (Date.parse(dates[i]) <= nowMs ? " (passed; refresh)" : ""));
    }
    var count = row.manual_resets_available;
    if (count == null) lines.push("Total reset count unavailable.");
    else if (dates.length < count)
        lines.push("Details returned for " + dates.length + " of " + count + " available resets.");
    else if (dates.length > count)
        lines.push("Details and reset balance differ; refresh to check.");
    return lines.join("\n");
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
            || !isFinite(row.used_percent) || row.used_percent < 0)
        return unknown;

    var observedMs = Date.parse(generatedAt);
    var resetMs = Date.parse(row.reset_at);
    if (!isFinite(observedMs) || !isFinite(resetMs) || !isFinite(nowMs)
            || observedMs > nowMs)
        return unknown;
    if (nowMs >= resetMs)
        return {level: "normal", hint: "This reset has passed. Refresh quota data."};
    if (row.used_percent >= 100)
        return {level: "red", hint: "Quota exhausted at the last refresh."};
    if (row.used_percent === 0)
        return {level: "normal", hint: "No quota used at the last refresh."};
    if (row.window_seconds === null)
        return {level: "normal", hint: "Pace unavailable: window length is unknown."};
    if (typeof row.window_seconds !== "number" || !isFinite(row.window_seconds)
            || row.window_seconds <= 0)
        return unknown;
    var windowMs = row.window_seconds * 1000;
    var elapsedMs = observedMs - (resetMs - windowMs);
    if (elapsedMs <= 0)
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
