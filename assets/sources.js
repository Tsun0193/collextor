(async function () {
  const table = document.querySelector("#sources-table tbody");
  if (!table || !window.Collextor) return;
  try {
    const data = await Collextor.loadJson("data/source-status.json");
    table.replaceChildren(...(data.sources || []).map((source) => {
      const status = source.ok ? "Succeeded" : (source.enabled ? "Failed" : "Unavailable");
      return Collextor.el("tr", {}, [
        Collextor.el("td", {}, Collextor.el("a", { href: source.page_url, target: "_blank", rel: "noopener noreferrer", text: source.name })),
        Collextor.el("td", { text: source.category }),
        Collextor.el("td", { text: source.source_type }),
        Collextor.el("td", { text: source.priority }),
        Collextor.el("td", { class: source.ok ? "status-ok" : "status-fail", text: status }),
        Collextor.el("td", { text: source.last_successful_fetch ? Collextor.fmtDate(source.last_successful_fetch, { time: true }) : source.message }),
      ]);
    }));
  } catch (err) {
    table.replaceChildren(Collextor.el("tr", {}, Collextor.el("td", { colspan: "6", text: "Source status is not available yet." })));
  }
})();
