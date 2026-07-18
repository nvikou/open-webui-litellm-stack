async function loadStatus() {
    const res = await fetch("/api/status");
    if (res.status === 401) {
        document.getElementById("globalBadge").textContent = "Auth requise";
        throw new Error("HTTP Basic Auth required");
    }
    const data = await res.json();
    const badge = document.getElementById("globalBadge");
    badge.textContent = data.all_ok
        ? "Infrastructure OK"
        : "Attention — service down";
    badge.className = "badge " + (data.all_ok ? "ok" : "bad");

    const services = document.getElementById("services");
    services.innerHTML = "";
    for (const s of data.services) {
        const el = document.createElement("div");
        el.className = "card";
        el.innerHTML =
            "<h2><span class='dot " +
            (s.ok ? "on" : "off") +
            "'></span>" +
            s.label +
            "</h2><p>" +
            s.detail +
            "</p>";
        services.appendChild(el);
    }

    const links = document.getElementById("links");
    links.innerHTML = "";
    const map = [
        ["Open WebUI", data.links.openwebui],
        ["LiteLLM", data.links.litellm],
        ["Authentik SSO", data.links.sso],
        ["Portail", data.links.portal],
        ["API Docs", data.links.docs],
    ];
    for (const [label, href] of map) {
        const a = document.createElement("a");
        a.className = "btn";
        a.href = href;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = label;
        links.appendChild(a);
    }

    document.getElementById("db").textContent = JSON.stringify(
        data.database || {},
        null,
        2
    );
}

async function loadBackups() {
    const res = await fetch("/api/backups");
    const data = await res.json();
    const box = document.getElementById("backupList");
    box.innerHTML = "";
    for (const item of data.items || []) {
        const row = document.createElement("div");
        row.className = "backup-item";
        const info = document.createElement("span");
        info.textContent =
            item.file + " · " + Math.round(item.size_bytes / 1024) + " KB";
        const btn = document.createElement("button");
        btn.className = "btn";
        btn.type = "button";
        btn.textContent = "Restore";
        btn.onclick = async () => {
            if (
                !confirm(
                    "Restore " +
                        item.file +
                        " ? Cela écrase l'état SQL actuel."
                )
            ) {
                return;
            }
            const msg = document.getElementById("msg");
            msg.textContent = "Restore en cours…";
            const r = await fetch("/api/restore", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ file: item.file }),
            });
            const out = await r.json();
            msg.textContent = out.ok
                ? "Restore OK → " + out.file
                : "Échec: " + (out.error || "unknown");
        };
        row.appendChild(info);
        row.appendChild(btn);
        box.appendChild(row);
    }
}

async function loadAlerts() {
    const res = await fetch("/api/alerts");
    const data = await res.json();
    document.getElementById("alerts").textContent = JSON.stringify(
        data.items || [],
        null,
        2
    );
}

async function refreshAll() {
    await loadStatus();
    await loadBackups();
    await loadAlerts();
}

document.getElementById("refreshBtn").onclick = () => {
    document.getElementById("msg").textContent = "Rafraîchissement…";
    refreshAll()
        .then(() => {
            document.getElementById("msg").textContent = "À jour.";
        })
        .catch((e) => {
            document.getElementById("msg").textContent = String(e);
        });
};

document.getElementById("backupBtn").onclick = async () => {
    const msg = document.getElementById("msg");
    msg.textContent = "Backup en cours…";
    try {
        const res = await fetch("/api/backup", { method: "POST" });
        const data = await res.json();
        msg.textContent = data.ok
            ? "Backup OK → " + data.file
            : "Échec: " + (data.error || "unknown");
        await loadBackups();
    } catch (e) {
        msg.textContent = String(e);
    }
};

refreshAll().catch((e) => {
    document.getElementById("globalBadge").textContent = "Erreur API";
    document.getElementById("msg").textContent = String(e);
});
