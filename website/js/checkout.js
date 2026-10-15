// Checkout page: order summary + delivery form validation.
function showError(field, message) {
  document.getElementById(field + "-error").textContent = message;
  document.getElementById(field).setAttribute("aria-invalid", message ? "true" : "false");
}

function validateCheckout(data) {
  const errors = {};
  if (!/[A-Za-z].*[A-Za-z]/.test(data.name) || /[<>]/.test(data.name)) errors.name = "Please enter your full name.";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(data.email)) errors.email = "Please enter a valid email address.";
  if (!/^\+?[0-9\s-]{7,15}$/.test(data.phone)) errors.phone = "Phone number must contain 7–15 digits.";
  if (data.address.length < 5) errors.address = "Please enter your street address.";
  if (!/^[0-9]{5}$/.test(data.zip)) errors.zip = "ZIP code must be exactly 5 digits.";
  const today = new Date().toISOString().slice(0, 10);
  if (!data.date || data.date <= today) errors.date = "Delivery date must be in the future.";
  return errors;
}

document.addEventListener("DOMContentLoaded", () => {
  const cart = getCart();
  const subtotal = cart.reduce((s, l) => s + findBook(l.id).price * l.qty, 0);
  document.getElementById("order-summary").textContent = cart.length
    ? cart.reduce((n, l) => n + l.qty, 0) + " item(s) · " + money(subtotal + SHIPPING) + " incl. shipping"
    : "Your cart is empty.";

  const form = document.getElementById("checkout-form");
  form.addEventListener("submit", e => {
    e.preventDefault();
    const data = {};
    ["name", "email", "phone", "address", "zip", "date"].forEach(f => data[f] = form[f].value.trim());
    const errors = validateCheckout(data);
    ["name", "email", "phone", "address", "zip", "date"].forEach(f => showError(f, errors[f] || ""));
    if (getCart().length === 0) {
      document.getElementById("form-message").textContent = "Your cart is empty — add a book first.";
      return;
    }
    if (Object.keys(errors).length > 0) return;
    form.classList.add("hidden");
    document.getElementById("order-done").classList.remove("hidden");
    document.getElementById("order-name").textContent = data.name;
    saveCart([]);
  });
});
