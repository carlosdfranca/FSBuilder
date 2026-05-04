document.addEventListener("DOMContentLoaded", () => {
    // ===============================
    // Theme Toggle (Claro / Escuro)
    // ===============================
    const themeToggle = document.getElementById('theme-toggle');
    const themeIcon   = document.getElementById('theme-icon');
    const html        = document.documentElement;

    function applyTheme(theme) {
        html.setAttribute('data-theme', theme);
        try { localStorage.setItem('cinnamon-theme', theme); } catch(e) {}
        if (themeIcon) {
            themeIcon.className = theme === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
        }
        if (themeToggle) {
            themeToggle.title = theme === 'dark' ? 'Mudar para modo claro' : 'Mudar para modo escuro';
        }
        // Troca favicon conforme tema
        const favicon = document.getElementById('favicon');
        if (favicon) {
            const base = favicon.href.substring(0, favicon.href.lastIndexOf('/') + 1);
            favicon.href = theme === 'light'
                ? base + 'simbolo-logo-lightmode.png'
                : base + 'simbolo-logo-darkmode.png';
        }
    }

    // Inicializa ícone conforme tema atual (já aplicado pelo script inline no <head>)
    const currentTheme = html.getAttribute('data-theme') || 'light';
    applyTheme(currentTheme);

    if (themeToggle) {
        themeToggle.addEventListener('click', function () {
            const next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            applyTheme(next);
        });
    }

    // ===============================
    // Navbar user dropdown toggle
    // ===============================
    document.querySelectorAll('.site-nav__user-btn[data-dropdown]').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            var targetId = btn.getAttribute('data-dropdown');
            var dropdown = document.getElementById(targetId);
            if (!dropdown) return;
            var isOpen = dropdown.classList.contains('show');
            // close all open dropdowns first
            document.querySelectorAll('.site-nav__dropdown.show').forEach(function (d) {
                d.classList.remove('show');
            });
            document.querySelectorAll('.site-nav__user-btn').forEach(function (b) {
                b.setAttribute('aria-expanded', 'false');
            });
            if (!isOpen) {
                dropdown.classList.add('show');
                btn.setAttribute('aria-expanded', 'true');
            }
        });
    });

    // close on outside click
    document.addEventListener('click', function () {
        document.querySelectorAll('.site-nav__dropdown.show').forEach(function (d) {
            d.classList.remove('show');
        });
        document.querySelectorAll('.site-nav__user-btn').forEach(function (b) {
            b.setAttribute('aria-expanded', 'false');
        });
    });
});
