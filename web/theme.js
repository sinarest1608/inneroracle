// Day/night theme: persisted, honours the OS preference on first load.

const THEME_KEY = 'inneroracle.theme';

function applyTheme(t){
  document.documentElement.dataset.theme = t;
  document.getElementById('theme').textContent = t === 'dark' ? '☀️' : '🌙';
  const tc = document.querySelector('meta[name="theme-color"]');
  if (tc) tc.content = t === 'dark' ? '#232c34' : '#f5eed9';   // matches header --ground-hi
}

export function initTheme(){
  applyTheme(localStorage.getItem(THEME_KEY) ||
    (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  document.getElementById('theme').onclick = ()=>{
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    localStorage.setItem(THEME_KEY, next); applyTheme(next);
  };
}
