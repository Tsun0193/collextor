(async function () {
  const root = document.getElementById("media-list");
  if (!root || !window.Collextor) return;
  try {
    const data = await Collextor.loadJson("data/media.json");
    const items = data.items || [];
    const meta = document.getElementById("media-meta");
    if (meta) meta.textContent = `${items.length} items / ${data.source_note || "curated public feeds"}`;
    if (!items.length) throw new Error("No media items");
    root.replaceChildren(...items.map((item) => mediaCard(item)));
  } catch (err) {
    root.replaceChildren(Collextor.el("div", { class: "notice", text: "No media items are available yet." }));
  }

  function mediaCard(item) {
    const article = {
      id: item.id,
      title: item.title,
      url: item.url,
      source_name: item.source_name,
      source_category: item.media_type || "media",
      section: "media",
      published_at: item.published_at,
      description: item.description,
      image_url: item.image_url,
    };
    return Collextor.el("article", { class: "media-card" }, [
      Collextor.articleHref(article, Collextor.imageVisual(article), "media-link"),
      Collextor.el("div", { class: "story-body" }, [
        Collextor.el("div", { class: "story-kicker" }, [
          Collextor.el("div", { class: "tag", text: item.media_type || "Media" }),
          Collextor.el("span", { class: "meta", text: item.source_name }),
        ]),
        Collextor.el("h2", {}, Collextor.safeLink(article, item.title)),
        Collextor.meta(article),
        item.description ? Collextor.el("p", { text: item.description }) : null,
      ]),
    ]);
  }
})();
