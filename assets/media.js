(async function () {
  const root = document.getElementById("media-list");
  const controls = document.getElementById("media-controls");
  const player = document.getElementById("media-player");
  if (!root || !window.Collextor) return;

  let items = [];
  let activeType = "All";
  let activeItem = null;
  const watchedStore = {
    key: "collextor.watchedMedia.v1",
    all() {
      try {
        return new Set(JSON.parse(localStorage.getItem(this.key) || "[]"));
      } catch (err) {
        return new Set();
      }
    },
    has(item) {
      return this.all().has(item.id);
    },
    add(item) {
      const watched = this.all();
      watched.add(item.id);
      localStorage.setItem(this.key, JSON.stringify(Array.from(watched).slice(-500)));
    },
    clear() {
      localStorage.removeItem(this.key);
    },
  };

  try {
    const data = await Collextor.loadJson("data/media.json");
    items = data.items || [];
    if (!items.length) throw new Error("No media items");
    activeItem = recommendedItems()[0] || items[0];
    renderControls();
    renderPlayer(activeItem);
    renderList();
  } catch (err) {
    root.replaceChildren(Collextor.el("div", { class: "notice", text: "No media items are available yet." }));
  }

  function renderControls() {
    if (!controls) return;
    const available = recommendedItems();
    const types = ["All", ...Array.from(new Set(available.map((item) => item.media_type || "Media")))];
    const watchedCount = items.filter((item) => watchedStore.has(item)).length;
    const actions = [Collextor.el("button", { class: "shuffle-button", type: "button", text: "Shuffle" })];
    if (watchedCount) {
      actions.push(Collextor.el("button", { class: "clear-watched-button", type: "button", text: "Reset watched" }));
    }
    controls.hidden = false;
    controls.replaceChildren(
      Collextor.el("div", { class: "filter-tabs", role: "tablist", "aria-label": "Media filters" }, types.map((type) => filterButton(type))),
      Collextor.el("div", { class: "media-actions" }, actions),
    );
    controls.querySelector(".shuffle-button").addEventListener("click", () => {
      const pool = filteredItems();
      if (!pool.length) return;
      activeItem = pool[Math.floor(Math.random() * pool.length)];
      renderPlayer(activeItem);
      renderList();
    });
    const clear = controls.querySelector(".clear-watched-button");
    if (clear) {
      clear.addEventListener("click", () => {
        watchedStore.clear();
        activeItem = filteredItems()[0] || items[0];
        renderControls();
        renderPlayer(activeItem);
        renderList();
      });
    }
  }

  function filterButton(type) {
    const selected = type === activeType;
    const button = Collextor.el("button", {
      class: selected ? "filter-button is-active" : "filter-button",
      type: "button",
      role: "tab",
      "aria-selected": selected ? "true" : "false",
      text: type,
    });
    button.addEventListener("click", () => {
      activeType = type;
      activeItem = filteredItems()[0] || items[0];
      renderControls();
      renderPlayer(activeItem);
      renderList();
    });
    return button;
  }

  function filteredItems() {
    const available = recommendedItems();
    return activeType === "All" ? available : available.filter((item) => (item.media_type || "Media") === activeType);
  }

  function recommendedItems() {
    return items.filter((item) => !watchedStore.has(item));
  }

  function renderPlayer(item) {
    if (!player || !item) return;
    player.hidden = false;
    const embed = youtubeEmbed(item.url);
    const visual = playerPoster(item, embed);
    player.replaceChildren(
      Collextor.el("div", { class: "player-frame" }, visual),
      Collextor.el("div", { class: "player-copy" }, [
        Collextor.el("div", { class: "story-kicker" }, [
          Collextor.el("div", { class: "tag", text: item.media_type || "Media" }),
          Collextor.el("span", { class: "meta", text: item.source_name }),
        ]),
        Collextor.el("h2", {}, Collextor.safeLink(asArticle(item), item.title)),
        Collextor.meta(asArticle(item)),
        item.description ? Collextor.el("p", { text: item.description }) : null,
      ]),
    );
  }

  function playerPoster(item, embed) {
    const article = asArticle(item);
    if (!embed) return Collextor.articleHref(article, Collextor.imageVisual(article), "media-link");
    const button = Collextor.el("button", { class: "player-poster", type: "button", "aria-label": `Play ${item.title}` }, [
      mediaVisual(item),
      Collextor.el("span", { class: "play-mark", text: "Play" }),
    ]);
    button.addEventListener("click", () => {
      watchedStore.add(item);
      button.replaceWith(Collextor.el("iframe", { src: `${embed}?autoplay=1&vq=hd1080&hd=1&modestbranding=1&rel=0`, title: item.title, loading: "lazy", allow: "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share", allowfullscreen: "true" }));
      renderControls();
      renderList();
    });
    return button;
  }

  function renderList() {
    const shown = filteredItems();
    if (!shown.length) {
      root.replaceChildren(Collextor.el("div", { class: "notice media-empty", text: "No unwatched media left in this filter." }));
      return;
    }
    root.replaceChildren(...shown.map((item) => mediaCard(item)));
  }

  function mediaCard(item) {
    const selected = activeItem && item.id === activeItem.id;
    const article = asArticle(item);
    const openButton = Collextor.el("button", { class: selected ? "media-pick is-active" : "media-pick", type: "button" }, [
      mediaVisual(item),
    ]);
    openButton.addEventListener("click", () => {
      activeItem = item;
      renderPlayer(item);
      renderList();
    });
    return Collextor.el("article", { class: selected ? "media-card is-active" : "media-card" }, [
      openButton,
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

  function asArticle(item) {
    return {
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
  }

  function mediaVisual(item) {
    const urls = thumbnailCandidates(item);
    if (!urls.length) return Collextor.imageVisual(asArticle(item));
    const img = Collextor.el("img", { src: urls[0], alt: item.title, loading: "lazy" });
    let index = 0;
    img.addEventListener("error", () => {
      index += 1;
      if (urls[index]) {
        img.src = urls[index];
      } else {
        img.parentElement.replaceWith(Collextor.imageVisual({ ...asArticle(item), image_url: "" }));
      }
    });
    return Collextor.el("div", { class: "image-frame" }, img);
  }

  function thumbnailCandidates(item) {
    const video = item.id || youtubeId(item.url);
    const candidates = [];
    if (item.image_url) candidates.push(item.image_url);
    if (video) {
      candidates.push(
        `https://img.youtube.com/vi/${video}/maxresdefault.jpg`,
        `https://img.youtube.com/vi/${video}/sddefault.jpg`,
        `https://img.youtube.com/vi/${video}/hqdefault.jpg`,
        `https://img.youtube.com/vi/${video}/mqdefault.jpg`,
      );
    }
    return Array.from(new Set(candidates));
  }

  function youtubeEmbed(url) {
    try {
      const parsed = new URL(url);
      const video = parsed.searchParams.get("v");
      if (!video) return "";
      return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(video)}`;
    } catch (err) {
      return "";
    }
  }

  function youtubeId(url) {
    try {
      return new URL(url).searchParams.get("v") || "";
    } catch (err) {
      return "";
    }
  }
})();
