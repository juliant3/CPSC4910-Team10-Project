// Navbar notifications dropdown.
// Opening it marks everything as read on the server and clears the badge;
// the "new" highlights stay visible until the next page load.
(function () {
    const menu = document.getElementById('notif-menu');
    if (!menu) return;

    const toggle = document.getElementById('notif-toggle');
    const panel = document.getElementById('notif-panel');
    let markedRead = false;

    function open() {
        panel.hidden = false;
        toggle.setAttribute('aria-expanded', 'true');
        toggle.classList.add('active');

        if (!markedRead && Number(toggle.dataset.unread) > 0) {
            markedRead = true;
            fetch(toggle.dataset.markReadUrl, { method: 'POST', credentials: 'same-origin' })
                .then(function (res) {
                    if (!res.ok) return;
                    const count = toggle.querySelector('.notif-count');
                    if (count) count.remove();
                    toggle.setAttribute('aria-label', 'Notifications');
                })
                .catch(function () { markedRead = false; });
        }
    }

    function close() {
        panel.hidden = true;
        toggle.setAttribute('aria-expanded', 'false');
        toggle.classList.remove('active');
    }

    toggle.addEventListener('click', function (e) {
        e.stopPropagation();
        panel.hidden ? open() : close();
    });

    document.addEventListener('click', function (e) {
        if (!panel.hidden && !menu.contains(e.target)) close();
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !panel.hidden) {
            close();
            toggle.focus();
        }
    });
})();
