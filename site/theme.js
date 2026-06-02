// Runs synchronously before first paint to prevent theme flash
(function(){
  var t = localStorage.getItem('phd_theme');
  if (t === 'light') document.documentElement.classList.add('theme-light');
  if (t === 'dark')  document.documentElement.classList.add('theme-dark');
})();
