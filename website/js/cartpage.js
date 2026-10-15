// Cart page: change quantities, remove items, show totals.
function renderCart() {
  const cart = getCart();
  const body = document.getElementById("cart-body");
  const empty = document.getElementById("cart-empty");
  const content = document.getElementById("cart-content");
  if (cart.length === 0) {
    empty.classList.remove("hidden");
    content.classList.add("hidden");
    return;
  }
  empty.classList.add("hidden");
  content.classList.remove("hidden");

  body.innerHTML = cart.map(line => {
    const b = findBook(line.id);
    return `<tr>
      <td>${b.icon} ${b.title}</td>
      <td>${money(b.price)}</td>
      <td><span class="qty">
        <button class="btn small secondary qty-minus" data-id="${b.id}" aria-label="Decrease quantity">−</button>
        <span>${line.qty}</span>
        <button class="btn small secondary qty-plus" data-id="${b.id}" aria-label="Increase quantity">+</button>
      </span></td>
      <td>${money(b.price * line.qty)}</td>
      <td><button class="btn small danger remove" data-id="${b.id}">Remove</button></td>
    </tr>`;
  }).join("");

  const subtotal = cart.reduce((s, l) => s + findBook(l.id).price * l.qty, 0);
  const shipping = SHIPPING;
  const total = subtotal + shipping;
  document.getElementById("subtotal").textContent = money(subtotal);
  document.getElementById("shipping").textContent = money(shipping);
  document.getElementById("total").textContent = money(total);
}

function changeQty(id, delta) {
  let cart = getCart();
  const line = cart.find(l => l.id === id);
  if (!line) return;
  line.qty += delta;
  if (line.qty <= 0) cart = cart.filter(l => l.id !== id);
  saveCart(cart);
  renderCart();
}

document.addEventListener("DOMContentLoaded", () => {
  renderCart();
  document.getElementById("cart-body").addEventListener("click", e => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (btn.classList.contains("qty-plus")) changeQty(id, 1);
    if (btn.classList.contains("qty-minus")) changeQty(id, -1);
    if (btn.classList.contains("remove")) changeQty(id, -Infinity);
  });
  document.getElementById("clear-cart").addEventListener("click", () => {
    saveCart([]);
    renderCart();
  });
});
