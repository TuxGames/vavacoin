/* Menu mobile (off-canvas) — vanilla, sem libs.

   Trazido do Benbals (static/menu.js) praticamente intacto: já era escrito
   com addEventListener, sem nenhum onclick no HTML, então passou direto pela
   CSP deste projeto. Abre/fecha a sidebar alternando body.sidebar-aberta.
   Fecha ao clicar num item, no backdrop, no X ou com ESC. Foco gerenciado
   (vai pro X ao abrir, volta pro hambúrguer ao fechar).

   É o ÚNICO JavaScript do VavaCoin, e não toca em nenhuma lógica de negócio:
   só navegação. Nada de dinheiro depende de script rodar. */
(function () {
  var body = document.body;
  var ham = document.querySelector(".hamburguer");
  var fecharBtn = document.querySelector(".sidebar-close");
  var backdrop = document.querySelector(".backdrop");
  var sidebar = document.getElementById("sidebar");

  function estaAberta() {
    return body.classList.contains("sidebar-aberta");
  }

  function abrir() {
    body.classList.add("sidebar-aberta");
    if (ham) ham.setAttribute("aria-expanded", "true");
    if (backdrop) backdrop.hidden = false;
    if (fecharBtn) fecharBtn.focus();
  }

  function fechar(devolverFoco) {
    body.classList.remove("sidebar-aberta");
    if (ham) ham.setAttribute("aria-expanded", "false");
    if (backdrop) backdrop.hidden = true;
    if (devolverFoco && ham) ham.focus();
  }

  if (ham) {
    ham.addEventListener("click", function () {
      estaAberta() ? fechar(true) : abrir();
    });
  }
  if (fecharBtn) fecharBtn.addEventListener("click", function () { fechar(true); });
  if (backdrop) backdrop.addEventListener("click", function () { fechar(false); });

  // Clicar num link do menu navega e fecha.
  if (sidebar) {
    sidebar.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function () { fechar(false); });
    });
  }

  // ESC fecha.
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && estaAberta()) fechar(true);
  });
})();
