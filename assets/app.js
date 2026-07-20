(function () {
  const TZ = "Asia/Bangkok";
  const cache = new Map();

  function dataUrl(path) {
    return `${path}${path.includes("?") ? "&" : "?"}v=${Date.now()}`;
  }

  async function loadJson(path) {
    if (!cache.has(path)) {
      cache.set(path, fetch(dataUrl(path), { cache: "no-store" }).then((res) => {
        if (!res.ok) throw new Error(`Could not load ${path}`);
        return res.json();
      }));
    }
    return cache.get(path);
  }

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value);
    }
    for (const child of [].concat(children)) {
      if (child) node.append(child);
    }
    return node;
  }

  function fmtDate(value, opts = {}) {
    const date = new Date(value);
    return new Intl.DateTimeFormat("en", { timeZone: TZ, month: "short", day: "numeric", hour: opts.time ? "numeric" : undefined, minute: opts.time ? "2-digit" : undefined }).format(date);
  }

  function relTime(value) {
    const then = new Date(value).getTime();
    const diff = Math.round((then - Date.now()) / 60000);
    const abs = Math.abs(diff);
    if (abs < 60) return `${abs || 1}m ago`;
    if (abs < 1440) return `${Math.round(abs / 60)}h ago`;
    return fmtDate(value);
  }

  function safeLink(article, text) {
    return el("a", { href: article.url, target: "_blank", rel: "noopener noreferrer", text });
  }

  function articleHref(article, children, className) {
    return el("a", { href: article.url, target: "_blank", rel: "noopener noreferrer", class: className }, children);
  }

  const savedStore = {
    key: "collextor.savedStories.v1",
    all() {
      try {
        return JSON.parse(localStorage.getItem(this.key) || "[]");
      } catch (err) {
        return [];
      }
    },
    has(article) {
      return this.all().some((item) => item.id === article.id);
    },
    toggle(article) {
      const current = this.all();
      const index = current.findIndex((item) => item.id === article.id);
      const active = index < 0;
      if (active) {
        current.unshift(savedArticle(article));
      } else {
        current.splice(index, 1);
      }
      localStorage.setItem(this.key, JSON.stringify(current.slice(0, 200)));
      window.dispatchEvent(new CustomEvent("collextor:saved-changed"));
      return active;
    },
  };

  function savedArticle(article) {
    const keys = ["id", "title", "url", "source_name", "source_category", "section", "published_at", "description", "image_url", "authors", "event_cluster_id"];
    const out = {};
    keys.forEach((key) => {
      if (article[key] !== undefined) out[key] = article[key];
    });
    out.saved_at = new Date().toISOString();
    return out;
  }

  function starButton(article) {
    const button = el("button", { class: "star-button", type: "button", title: "Save story", "aria-label": `Save ${article.title}` });
    function sync() {
      const active = savedStore.has(article);
      button.textContent = active ? "★" : "☆";
      button.classList.toggle("is-saved", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
      button.title = active ? "Remove from Saved" : "Save story";
    }
    button.addEventListener("click", () => {
      savedStore.toggle(article);
      sync();
    });
    sync();
    return button;
  }

  function storyKicker(article, text) {
    return el("div", { class: "story-kicker" }, [
      el("div", { class: "tag", text }),
      starButton(article),
    ]);
  }

  function meta(article) {
    const span = el("div", { class: "meta" });
    span.textContent = `${article.source_name} / ${relTime(article.published_at)}`;
    span.title = fmtDate(article.published_at, { time: true });
    return span;
  }

  function imageVisual(article) {
    if (!article.image_url) {
      return el("div", { class: `placeholder placeholder-${article.section || "default"}` }, [
        el("span", { class: "placeholder-kicker", text: label(article) }),
        el("span", { class: "placeholder-source", text: article.source_name || "COLLEXTOR" }),
      ]);
    }
    const img = el("img", { src: article.image_url, alt: article.title, loading: "lazy" });
    img.addEventListener("error", () => img.parentElement.replaceWith(imageVisual({ ...article, image_url: "" })));
    return el("div", { class: "image-frame" }, img);
  }

  function story(article, opts = {}) {
    const h = el(opts.large ? "h1" : "h3", {}, safeLink(article, article.title));
    const klass = opts.large ? "lead-story" : (opts.featured ? "story featured-story" : "story");
    const body = el("div", { class: "story-body" }, [
      storyKicker(article, label(article)),
      h,
      meta(article),
      article.description ? el("p", { text: article.description }) : null,
    ]);
    return el("article", { class: klass }, [
      opts.image ? articleHref(article, imageVisual(article), "media-link") : null,
      body,
    ]);
  }

  function sectionStories(id, items, limit, registry, imageFallbacks = []) {
    const node = document.getElementById(id);
    const section = node && node.closest(".section-block");
    if (!node) return;
    node.replaceChildren();
    const chosen = takeUnique(preferImageLead(items, imageFallbacks, registry), limit, registry);
    if (!chosen.length && section) {
      section.hidden = true;
      return;
    }
    node.append(story(chosen[0], { image: true, featured: true }));
    chosen.slice(1).forEach((item) => node.append(story(item)));
  }

  function research(article, opts = {}) {
    const authors = article.authors && article.authors.length ? article.authors.slice(0, 3).join(", ") : "";
    const body = el("div", { class: "story-body" }, [
      storyKicker(article, article.source_name),
      el("h3", {}, safeLink(article, article.title)),
      el("div", { class: "meta", text: [fmtDate(article.published_at), authors].filter(Boolean).join(" / ") }),
      article.description ? el("p", { text: article.description }) : null,
    ]);
    return el("article", { class: opts.featured ? "research-card featured-story" : "research-card" }, [
      opts.image ? articleHref(article, imageVisual(article), "media-link") : null,
      body,
    ]);
  }

  function sectionResearch(id, items, limit, registry, imageFallbacks = []) {
    const node = document.getElementById(id);
    const section = node && node.closest(".section-block");
    if (!node) return;
    node.replaceChildren();
    const chosen = takeUnique(preferImageLead(items, imageFallbacks, registry), limit, registry);
    if (!chosen.length && section) {
      section.hidden = true;
      return;
    }
    node.append(research(chosen[0], { image: true, featured: true }));
    chosen.slice(1).forEach((item) => node.append(research(item)));
  }

  function label(article) {
    const map = {
      medical_neuroimaging: "Medical AI",
      multimodal_foundation: "Research",
      startup_product: "Product",
      business_policy: "Business",
      ai_engineering: "Engineering",
      long_reads: "Long Read",
      media: "Media",
    };
    return map[article.section] || article.source_category || "Story";
  }

  function setList(id, items, renderer, limit) {
    const node = document.getElementById(id);
    const section = node && node.closest(".section-block");
    if (!node) return;
    node.replaceChildren();
    const chosen = items.slice(0, limit);
    if (!chosen.length && section) {
      section.hidden = true;
      return;
    }
    chosen.forEach((item) => node.append(renderer(item)));
  }

  async function initDaily() {
    const front = document.getElementById("front-page");
    if (!front) return;
    try {
      const latest = await loadJson("data/latest.json");
      const history = await loadJson("data/history.json").catch(() => ({ articles: [] }));
      document.getElementById("today").textContent = latest.date_label || new Intl.DateTimeFormat("en", { timeZone: TZ, dateStyle: "full" }).format(new Date());
      document.getElementById("update-line").textContent = `Last successful feed update: ${fmtDate(latest.generated_at, { time: true })}`;
      const articles = latest.articles || [];
      if (!articles.length) throw new Error("No retained articles are available.");
      const registry = createStoryRegistry();
      const breaking = articles.filter((a) => a.is_breaking).slice(0, 3);
      const breakingNode = document.getElementById("breaking");
      if (breaking.length) {
        breakingNode.hidden = false;
        breakingNode.replaceChildren(el("strong", { text: "Breaking" }), ...breaking.map((a) => safeLink(a, a.title)));
      }
      const historyArticles = history.articles || [];
      const visualPool = uniqueClusters([...articles, ...historyArticles.filter((a) => a.image_url)]);
      const frontVisualFallbacks = visualPool.filter((a) => !["research", "long-read"].includes(a.source_category) && !a.research_track);
      const frontUnique = uniqueClusters(preferImageLead(articles, frontVisualFallbacks));
      const lead = frontUnique.find((a) => !["research", "long-read"].includes(a.source_category) && !a.research_track) || frontUnique[0];
      const secondaries = frontUnique.filter((a) => a.id !== lead.id).slice(0, 4);
      [lead, ...secondaries].forEach((item) => registry.mark(item));
      front.replaceChildren(story(lead, { large: true, image: true }), el("div", { class: "secondary-list" }, secondaries.map((a) => story(a))));
      await renderStocks();
      sectionStories("must-read", articles.filter((a) => a.is_must_read), 6, registry, visualPool.filter((a) => a.is_must_read));
      sectionStories("ai-engineering", articles.filter((a) => a.section === "ai_engineering"), 8, registry, visualPool.filter((a) => a.section === "ai_engineering"));
      sectionResearch("medical-neuroimaging", articles.filter((a) => a.section === "medical_neuroimaging"), 5, registry, visualPool.filter((a) => a.section === "medical_neuroimaging"));
      sectionResearch("multimodal-foundation", articles.filter((a) => a.section === "multimodal_foundation"), 5, registry, visualPool.filter((a) => a.section === "multimodal_foundation"));
      sectionStories("startup-product", articles.filter((a) => a.section === "startup_product"), 8, registry, visualPool.filter((a) => a.section === "startup_product"));
      sectionStories("business-policy", articles.filter((a) => a.section === "business_policy"), 8, registry, visualPool.filter((a) => a.section === "business_policy"));
    } catch (err) {
      front.replaceChildren();
      const failure = document.getElementById("failure");
      failure.hidden = false;
      failure.textContent = "The latest edition could not be loaded. Please try again after the next scheduled update.";
    }
  }

  async function renderStocks() {
    const node = document.getElementById("stock-strip");
    const section = node && node.closest(".section-block");
    if (!node) return;
    try {
      const data = await loadJson("data/stocks.json");
      const symbols = data.symbols || [];
      if (!symbols.length) {
        if (section) section.hidden = true;
        return;
      }
      const metaNode = document.getElementById("stocks-meta");
      if (metaNode) metaNode.textContent = `${data.source?.name || "Market data"} / delayed quotes`;
      node.replaceChildren(...symbols.map((quote) => {
        const price = typeof quote.price === "number" ? quote.price.toLocaleString("en-US", { style: "currency", currency: quote.currency || "USD" }) : "N/A";
        const change = typeof quote.change_percent === "number" ? `${quote.change_percent >= 0 ? "+" : ""}${quote.change_percent.toFixed(2)}%` : "";
        const changeClass = quote.change_percent > 0 ? "stock-change up" : quote.change_percent < 0 ? "stock-change down" : "stock-change";
        return el("a", { class: "stock-tile", href: quote.url, target: "_blank", rel: "noopener noreferrer" }, [
          el("span", { class: "stock-symbol", text: quote.symbol }),
          el("span", { class: "stock-name", text: quote.name }),
          el("span", { class: "stock-price", text: price }),
          change ? el("span", { class: changeClass, text: change }) : null,
          el("span", { class: "stock-date", text: quote.date || "" }),
        ]);
      }));
      renderMarketBrief(data.analysis || {});
    } catch (err) {
      if (section) section.hidden = true;
    }
  }

  function renderMarketBrief(analysis) {
    const node = document.getElementById("market-brief");
    if (!node) return;
    const bullets = analysis.bullets || [];
    const related = analysis.related_articles || [];
    if (!analysis.headline && !bullets.length) {
      node.replaceChildren();
      return;
    }
    const bulletList = el("ul", {}, bullets.map((text) => el("li", { text })));
    const relatedList = related.length ? el("div", { class: "market-related" }, [
      el("span", { class: "meta", text: "Related reads" }),
      ...related.map((item) => el("a", { href: item.url, target: "_blank", rel: "noopener noreferrer", text: `${item.title} / ${item.source_name}` })),
    ]) : null;
    node.replaceChildren(
      el("div", { class: "tag", text: "Financial Brief" }),
      el("h3", { text: analysis.headline || "Market context" }),
      bulletList,
      relatedList,
      analysis.disclaimer ? el("p", { class: "market-disclaimer", text: analysis.disclaimer }) : null,
    );
  }

  function uniqueClusters(articles) {
    const seen = new Set();
    return articles.filter((a) => {
      const key = a.event_cluster_id || a.id;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function createStoryRegistry() {
    const seen = new Set();
    return {
      key(article) {
        return article.event_cluster_id || article.canonical_url || article.url || article.id;
      },
      has(article) {
        return seen.has(this.key(article));
      },
      mark(article) {
        seen.add(this.key(article));
      },
    };
  }

  function takeUnique(articles, limit, registry) {
    const chosen = [];
    for (const article of uniqueClusters(articles)) {
      if (registry && registry.has(article)) continue;
      chosen.push(article);
      if (registry) registry.mark(article);
      if (chosen.length >= limit) break;
    }
    return chosen;
  }

  function preferImageLead(primary, fallbacks = [], registry) {
    const primaryUnique = uniqueClusters(primary);
    if (primaryUnique[0] && primaryUnique[0].image_url) return primaryUnique;
    const imageLead = uniqueClusters([...primaryUnique, ...fallbacks]).find((article) => article.image_url && (!registry || !registry.has(article)));
    if (!imageLead) return primaryUnique;
    return [imageLead, ...primaryUnique.filter((article) => article.id !== imageLead.id)];
  }

  window.Collextor = { loadJson, el, story, research, fmtDate, safeLink, articleHref, imageVisual, meta, label, savedStore };
  document.addEventListener("DOMContentLoaded", initDaily);
})();
