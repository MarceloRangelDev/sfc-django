# Template Settings
# ------------------------------------------------------------------------------


# Theme layout templates directory

# Template config
# ? Easily change the template configuration from here
# ? Replace this object with template-config/demo-*.py file's TEMPLATE_CONFIG to change the template configuration as per our demos
TEMPLATE_CONFIG = {
    "layout": "vertical",             # Options[String]: vertical(default), horizontal
    "primary_color": "#0D9394",     # Set the primary color
    "theme": 'light',                 # Theme options: 'light' (default), 'dark', 'system'
    "my_skins" : "default",           # Skin options: "default", "bordered"
    "has_semi_dark" : False,          # Semi-dark mode: True/False (False by default)
    "rtl_mode": False,                # options[Boolean]: False(default), True # To set layout to RTL layout  (myRTLSupport must be True for rtl mode)
    "has_customizer": True,           # options[Boolean]: True(default), False # Display customizer or not THIS WILL REMOVE INCLUDED JS FILE. SO LOCAL STORAGE WON'T WORK
    "display_customizer": True,       # options[Boolean]: True(default), False # Display customizer UI or not, THIS WON'T REMOVE INCLUDED JS FILE. SO LOCAL STORAGE WILL WORK
    "content_layout": "compact",      # options[String]: 'compact', 'wide' (compact=container-xxl, wide=container-fluid)
    "navbar_type": "fixed",           # options[String]: 'fixed', 'static', 'hidden' (Only for vertical Layout)
    "header_type": "fixed",           # options[String]: 'static', 'fixed' (for horizontal layout only)
    "menu_fixed": True,               # options[Boolean]: True(default), False # Layout(menu) Fixed (Only for vertical Layout)
    "menu_collapsed": False,          # options[Boolean]: False(default), True # Show menu collapsed, Only for vertical Layout
    "footer_fixed": False,            # options[Boolean]: False(default), True # Footer Fixed
    "show_dropdown_onhover": True,    # True, False (for horizontal layout only)
    "customizer_controls": [
        'color', # Enable/Disable color picker in customizer
        'theme', # Enable/Disable theme selection in customizer
        'skins', # Enable/Disable skin options in customizer
        'semiDark', # Enable/Disable semi-dark mode in customizer
        'layoutCollapsed', # Enable/Disable collapsed layout in customizer
        'layoutNavbarOptions', # Enable/Disable navbar options in customizer
        'headerType', # Enable/Disable header type selection in customizer
        'contentLayout', # Enable/Disable content layout options in customizer
        'rtl'
    ],  # To show/hide customizer options
}


# Theme Variables
# ? Personalize template by changing theme variables (For ex: Name, URL Version etc...)
THEME_VARIABLES = {
    "creator_name": "SFC",
    "creator_url": "https://sistemafaturandocerto.com/",
    "template_name": "SFC",
    "template_suffix": "Sistema Faturando Certo",
    "template_version": "1.0.0",
    "template_free": False,
    "template_description": "SFC is a modern, clean and fully responsive admin template built with Bootstrap 5, Django, HTML, CSS, jQuery, and JavaScript. It has a huge collection of reusable UI components and integrated with the latest jQuery plugins. It can be used for all types of web applications like custom admin panel, project management system, admin dashboard, Backend application or CRM.",
    "template_keyword": "django, django admin, dashboard, bootstrap 5 dashboard, bootstrap 5 design, bootstrap 5",
    "facebook_url": "https://www.facebook.com/pixinvents/",
    "twitter_url": "https://twitter.com/pixinvents",
    "github_url": "https://github.com/pixinvent",
    "dribbble_url": "https://dribbble.com/pixinvent",
    "instagram_url": "https://www.instagram.com/pixinvents/",
    "license_url": "https://themeforest.net/licenses/standard",
    "live_preview": "https://demos.pixinvent.com/vuexy-html-django-admin-template/demo-1/",
    "product_page": "https://1.envato.market/vuexy_admin",
    "support": "https://pixinvent.ticksy.com/",
    "more_themes": "https://1.envato.market/pixinvent_portfolio",
    "documentation": "https://demos.pixinvent.com/vuexy-html-admin-template/documentation",
    "changelog": "https://demos.pixinvent.com/vuexy/changelog.html",
    "git_repository": "https://github.com/pixinvent/vuexy-html-django-admin-template",
    "git_repo_access": "https://tools.pixinvent.com/github/github-access",
    "og_title": "Vuexy Bootstrap 5 HTML + Django Admin Template by Pixinvent",
    "og_image": "https://ts-assets.b-cdn.net/pi-assets/vuexy/admin-template/banner/banner.png",
    "og_type": "product",
}

# ! Don't change THEME_LAYOUT_DIR unless it's required
THEME_LAYOUT_DIR = "layout"
