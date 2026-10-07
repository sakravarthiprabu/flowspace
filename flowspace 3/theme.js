// Apply the saved theme before first paint. No external dependencies.
(()=>{
 const key='flowspace-theme';
 function preferred(){try{const saved=localStorage.getItem(key);if(['light','dark'].includes(saved))return saved;}catch{}return window.matchMedia?.('(prefers-color-scheme: dark)').matches?'dark':'light';}
 function apply(theme){
  theme=theme==='dark'?'dark':'light';document.documentElement.dataset.theme=theme;document.documentElement.style.colorScheme=theme;
  try{localStorage.setItem(key,theme);}catch{}
  document.querySelectorAll('[data-theme-icon]').forEach(el=>el.textContent=theme==='dark'?'☀':'☾');
  document.querySelectorAll('[data-theme-label]').forEach(el=>el.textContent=theme==='dark'?'Light mode':'Dark mode');
  document.querySelectorAll('[data-action="toggle-theme"]').forEach(el=>{el.setAttribute('aria-label',`Switch to ${theme==='dark'?'light':'dark'} theme`);el.setAttribute('aria-pressed',String(theme==='dark'));});
 }
 window.FlowSpaceTheme={apply};apply(preferred());document.addEventListener('DOMContentLoaded',()=>apply(document.documentElement.dataset.theme));
})();
