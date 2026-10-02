/**
 * Registra o idioma pt-br no Template Customizer do Vuexy.
 *
 * Por que existe: o template-customizer.js do Vuexy só traz en, fr, ar e de.
 * Como o projeto usa LANGUAGE_CODE = "pt-br", o customizer lançava
 * 'Language "pt-br" not found!' ao ser iniciado. Em vez de editar o arquivo
 * do fornecedor (que se perderia numa atualização do tema), as traduções são
 * registradas aqui, DEPOIS de template-customizer.js e ANTES de
 * "new TemplateCustomizer(...)" (ver templates/layout/partials/scripts_includes.html).
 */
(function () {
  if (typeof TemplateCustomizer === 'undefined') return;

  TemplateCustomizer.LANGUAGES['pt-br'] = {
    panel_header: 'Personalizador de Tema',
    panel_sub_header: 'Personalize e visualize em tempo real',
    theming_header: 'Tema',
    color_label: 'Cor primária',
    theme_label: 'Tema',
    skin_label: 'Estilos',
    semiDark_label: 'Semi escuro',
    layout_header: 'Layout',
    layout_label: 'Menu (Navegação)',
    layout_header_label: 'Tipos de cabeçalho',
    content_label: 'Conteúdo',
    layout_navbar_label: 'Tipo de barra de navegação',
    direction_label: 'Direção'
  };
})();
