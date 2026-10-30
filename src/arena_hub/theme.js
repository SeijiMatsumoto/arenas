const button = document.querySelector('#theme');
let theme;
try { theme = localStorage.getItem('arenas-theme'); } catch { /* Storage is optional. */ }
if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
function updateButton() {
  const dark = document.documentElement.dataset.theme === 'dark' ||
    (!document.documentElement.dataset.theme && matchMedia('(prefers-color-scheme: dark)').matches);
  button.textContent = dark ? 'Light theme' : 'Dark theme';
  button.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
}
button.hidden = false;
updateButton();
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', updateButton);
button.addEventListener('click', () => {
  const dark = document.documentElement.dataset.theme === 'dark' ||
    (!document.documentElement.dataset.theme && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'light' : 'dark';
  try { localStorage.setItem('arenas-theme', document.documentElement.dataset.theme); } catch { /* Optional. */ }
  updateButton();
});
