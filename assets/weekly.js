(async function () {
  const root = document.getElementById("weekly-content");
  if (!root || !window.Collextor) return;
  const params = new URLSearchParams(location.search);
  try {
    let week = params.get("week");
    if (!week) {
      const index = await Collextor.loadJson("data/archive-index.json");
      week = index.editions && index.editions[0] && index.editions[0].week_id;
    }
    if (!week) throw new Error("No weekly edition is available.");
    const data = await Collextor.loadJson(`data/weekly/${week}.json`);
    document.getElementById("week-id").textContent = `ISO Week ${week}`;
    document.getElementById("week-range").textContent = data.date_range || week;
    const articles = data.articles || [];
    const sections = [
      ["Story of the Week", articles.slice(0, 1)],
      ["Top Stories", articles.slice(1, 7)],
      ["Research Worth Knowing", articles.filter((a) => a.research_track).slice(0, 8)],
      ["Engineering & Infrastructure", articles.filter((a) => a.section === "ai_engineering").slice(0, 8)],
      ["Startup & Product Ideas", articles.filter((a) => a.section === "startup_product").slice(0, 6)],
      ["Business & Policy", articles.filter((a) => a.section === "business_policy").slice(0, 6)],
      ["Long Reads", articles.filter((a) => a.is_long_read).slice(0, 6)],
    ];
    root.replaceChildren(...sections.filter(([, items]) => items.length).map(([title, items]) => {
      const block = Collextor.el("section", { class: "weekly-section" }, [Collextor.el("h2", { text: title })]);
      const list = Collextor.el("div", { class: "story-list compact" });
      items.forEach((item) => list.append(Collextor.story(item)));
      block.append(list);
      return block;
    }));
  } catch (err) {
    root.replaceChildren(Collextor.el("div", { class: "notice", text: "No weekly edition is available yet." }));
  }
})();
