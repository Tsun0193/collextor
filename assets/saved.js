(function () {
  function renderSaved() {
    const root = document.getElementById("saved-list");
    if (!root || !window.Collextor) return;
    const saved = Collextor.savedStore.all();
    if (!saved.length) {
      root.classList.remove("story-list", "compact");
      root.replaceChildren(Collextor.el("div", { class: "notice", text: "No starred stories yet." }));
      return;
    }
    root.classList.add("story-list", "compact");
    root.replaceChildren(...saved.map((article) => Collextor.story(article)));
  }

  document.addEventListener("DOMContentLoaded", renderSaved);
  window.addEventListener("collextor:saved-changed", renderSaved);
})();
