// Native POSTs preserve server-side validation, session handling and rate limits.
document.querySelectorAll('[data-password-toggle]').forEach(button => {
  button.addEventListener('click', () => {
    const input = button.parentElement.querySelector('input');
    const visible = input.type === 'password';
    input.type = visible ? 'text' : 'password';
    button.textContent = visible ? 'OCULTAR' : 'MOSTRAR';
    button.setAttribute('aria-pressed', String(visible));
    button.setAttribute('aria-label', visible ? 'Ocultar senha' : 'Mostrar senha');
  });
});
document.querySelector('[data-auth-form="reset"]')?.addEventListener('submit', event => {
  const form = event.currentTarget;
  if (form.elements.password.value !== form.elements.password2.value) {
    event.preventDefault();
    const error = document.querySelector('[data-auth-error]');
    error.textContent = 'As senhas precisam ser iguais.';
    error.hidden = false;
    form.elements.password2.focus();
  }
});
