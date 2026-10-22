// ==========================================
// DRIVERS ADVANTAGE - SESSION TIMEOUT
// ==========================================

const SESSION_LIMIT = 5 * 60 * 1000;
const WARNING_TIME = 4 * 60 * 1000;

let lastActivity = Date.now();
let warningVisible = false;
let keepAlivePending = false;

// Create the warning popup
const overlay = document.createElement("div");
overlay.id = "session-warning-overlay";

overlay.innerHTML = `
    <div class="session-warning-box">
        <div class="session-warning-icon">!</div>

        <h2>Your Session Is About to Expire</h2>

        <p>
            You've been inactive for a while.
            For your security, you'll be logged out
            automatically.
        </p>

        <div id="session-countdown">01:00</div>

        <p class="session-countdown-label">
            Time remaining
        </p>

        <div class="session-warning-buttons">
            <button
                id="stay-logged-in"
                type="button">
                Stay Logged In
            </button>

            <button
                id="logout-now"
                type="button">
                Log Out
            </button>
        </div>
    </div>
`;

document.body.appendChild(overlay);

// Show warning
function showSessionWarning() {
    warningVisible = true;
    overlay.style.display = "flex";
}

// Hide warning
function hideSessionWarning() {
    warningVisible = false;
    overlay.style.display = "none";
}

// Log out
function logoutUser() {
    window.location.replace("/logout");
}

// Record activity without automatically
// dismissing the warning.
function recordActivity() {
    if (!warningVisible && !keepAlivePending) {
        lastActivity = Date.now();
    }
}

// Watch inactivity
setInterval(() => {
    const idleTime = Date.now() - lastActivity;

    if (idleTime >= SESSION_LIMIT) {
        logoutUser();
        return;
    }

    if (idleTime >= WARNING_TIME && !warningVisible) {
        showSessionWarning();
    }

    if (warningVisible) {
        const remaining = Math.max(
            0,
            Math.ceil((SESSION_LIMIT - idleTime) / 1000)
        );

        const minutes = Math.floor(remaining / 60);
        const seconds = remaining % 60;

        document.getElementById("session-countdown").textContent =
            `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    }
}, 250);

// Stay Logged In button
document.getElementById("stay-logged-in")
    .addEventListener("click", async () => {

        if (keepAlivePending) return;

        keepAlivePending = true;

        try {
            const response = await fetch("/session/keep-alive", {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "X-Requested-With": "XMLHttpRequest"
                }
            });

            if (!response.ok) {
                logoutUser();
                return;
            }

            const data = await response.json();

            if (!data.success) {
                logoutUser();
                return;
            }

            lastActivity = Date.now();
            hideSessionWarning();

        } catch (error) {
            logoutUser();
        } finally {
            keepAlivePending = false;
        }
    });

// Log Out button
document.getElementById("logout-now")
    .addEventListener("click", logoutUser);

// Track user interaction
["mousemove", "mousedown", "keydown", "scroll", "touchstart"]
    .forEach(eventName => {
        document.addEventListener(eventName, recordActivity, {
            passive: true
        });
    });