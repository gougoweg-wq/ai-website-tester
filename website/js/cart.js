// Book catalogue and shared cart helpers (cart is kept in localStorage).
const BOOKS = [
  { id: 1, title: "The Silent Forest", author: "Mara Quinn", genre: "Fiction", price: 12.99, icon: "🌲", color: "#3f7d5c" },
  { id: 2, title: "Code of the Stars", author: "Leo Park", genre: "Sci-Fi", price: 15.5, icon: "🚀", color: "#34507a" },
  { id: 3, title: "Bread & Butter", author: "Ana Silva", genre: "Cooking", price: 9.99, icon: "🍞", color: "#b07a3c" },
  { id: 4, title: "Mind Over Matter", author: "Dr. K. Ito", genre: "Science", price: 18.0, icon: "🧠", color: "#7a3f6b" },
  { id: 5, title: "The Last Lighthouse", author: "Mara Quinn", genre: "Fiction", price: 11.25, icon: "🗼", color: "#2e6f86" },
  { id: 6, title: "Robots Dream Too", author: "Leo Park", genre: "Sci-Fi", price: 13.75, icon: "🤖", color: "#55606e" },
  { id: 7, title: "Soup for Every Season", author: "Ana Silva", genre: "Cooking", price: 8.5, icon: "🍲", color: "#a5523a" },
  { id: 8, title: "Tiny Universe", author: "Dr. K. Ito", genre: "Science", price: 16.4, icon: "🔭", color: "#3d3d78" }
];
const SHIPPING = 4.99;

function getCart() {
  try { return JSON.parse(localStorage.getItem("cart")) || []; } catch (e) { return []; }
}
function saveCart(cart) {
  localStorage.setItem("cart", JSON.stringify(cart));
  updateBadge();
}
function addToCart(id) {
  const cart = getCart();
  const line = cart.find(l => l.id === id);
  if (line) line.qty += 1; else cart.push({ id: id, qty: 1 });
  saveCart(cart);
}
function findBook(id) { return BOOKS.find(b => b.id === id); }
function money(n) { return "$" + n.toFixed(2); }
function updateBadge() {
  const el = document.getElementById("cart-count");
  if (el) el.textContent = getCart().reduce((s, l) => s + l.qty, 0);
}
document.addEventListener("DOMContentLoaded", updateBadge);
