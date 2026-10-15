// Shop page: search, genre filter, sorting and "Add to cart".
let query = "";

function renderBooks() {
  const genre = document.getElementById("genre").value;
  const sort = document.getElementById("sort").value;
  let list = BOOKS.filter(b =>
    (genre === "all" || b.genre === genre) &&
    (query === "" || b.title.toLowerCase().includes(query) || b.author.toLowerCase().includes(query))
  );
  if (sort === "price-asc") list.sort((a, b) => a.price - b.price);
  if (sort === "price-desc") list.sort((a, b) => b.price - a.price);
  if (sort === "title") list.sort((a, b) => a.title.localeCompare(b.title));

  document.getElementById("result-count").textContent = list.length + " book(s) found";
  document.getElementById("book-grid").innerHTML = list.map(b => `
    <article class="card">
      <div class="cover" style="background:${b.color}">${b.icon}</div>
      <h3>${b.title}</h3>
      <p class="author">${b.author} · ${b.genre}</p>
      <p class="price">${money(b.price)}</p>
      <button class="btn add-to-cart" data-id="${b.id}">Add to cart</button>
    </article>`).join("");
}

document.addEventListener("DOMContentLoaded", () => {
  renderBooks();
  document.getElementById("search-form").addEventListener("submit", e => {
    e.preventDefault();
    query = document.getElementById("search").value.trim().toLowerCase();
    renderBooks();
  });
  document.getElementById("genre").addEventListener("change", renderBooks);
  document.getElementById("sort").addEventListener("change", renderBooks);
  document.getElementById("book-grid").addEventListener("click", e => {
    const btn = e.target.closest(".add-to-cart");
    if (!btn) return;
    addToCart(Number(btn.dataset.id));
    btn.textContent = "Added ✓";
  });
});
