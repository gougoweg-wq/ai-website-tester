// Contact page: message form validation.
document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("contact-form");
  form.addEventListener("submit", e => {
    e.preventDefault();
    const name = form.cname.value.trim();
    const email = form.cemail.value.trim();
    const message = form.message.value.trim();
    const errors = {
      cname: !/[A-Za-z].*[A-Za-z]/.test(name) || /[<>]/.test(name) ? "Please tell us your name." : "",
      cemail: /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ? "" : "Please enter a valid email address.",
      message: message.length < 10 ? "Message must be at least 10 characters." : ""
    };
    Object.keys(errors).forEach(f => document.getElementById(f + "-error").textContent = errors[f]);
    if (Object.values(errors).some(Boolean)) return;
    form.classList.add("hidden");
    document.getElementById("contact-done").classList.remove("hidden");
  });
});
