// Tiny shop for the browser-agent e2e test. Login state and cart live in localStorage.
const S = {
  get user() { return localStorage.getItem('pm_user'); },
  get cart() { return JSON.parse(localStorage.getItem('pm_cart') || '[]'); },
  add(id) { const c = S.cart; c.push(id); localStorage.setItem('pm_cart', JSON.stringify(c)); },
};
const PRODUCTS = { 'blue-pen': 'Blue Ballpoint Pen', 'red-pen': 'Red Gel Pen', 'notebook': 'A5 Notebook' };
const cc = document.getElementById('cartCount');
if (cc) cc.textContent = S.cart.length;
const login = document.getElementById('loginLink');
if (login && S.user) { login.textContent = 'My Account'; login.href = 'cart.html'; }
const sb = document.getElementById('searchBtn');
if (sb) sb.onclick = () => {
  const q = document.getElementById('search').value.toLowerCase();
  document.querySelectorAll('.card').forEach(c => c.style.display = c.innerText.toLowerCase().includes(q.split(' ')[0]) ? '' : 'none');
};
const s = document.getElementById('search');
if (s) s.addEventListener('keydown', e => { if (e.key === 'Enter') sb.click(); });
