(async function () {
  const root = document.getElementById("archive-list");
  if (!root || !window.Collextor) return;
  try {
    const data = await Collextor.loadJson("data/archive-index.json");
    const editions = data.editions || [];
    if (!editions.length) throw new Error("empty archive");
    root.replaceChildren(...editions.map((edition) => Collextor.el("article", { class: "archive-card" }, [
      Collextor.el("div", { class: "meta", text: `${edition.week_id} / ${edition.date_range} / ${edition.story_count} stories` }),
      Collextor.el("h2", {}, Collextor.el("a", { href: edition.url, text: edition.lead_headline || "Weekly edition" })),
    ])));
  } catch (err) {
    root.replaceChildren(Collextor.el("div", { class: "notice", text: "No archived weekly editions are available yet." }));
  }
})();
