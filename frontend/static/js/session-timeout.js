// =========================================================
// AUTOMATIC LOGOUT AFTER 5 MINUTES OF INACTIVITY
// =========================================================

const TIMEOUT_DURATION = 5 * 60 * 1000;

let inactivityTimer;

function logoutUser() {
    window.location.href = "/logout";
}

function resetInactivityTimer() {
    clearTimeout(inactivityTimer);

    inactivityTimer = setTimeout(
        logoutUser,
        TIMEOUT_DURATION
    );
}

// User activity resets the timer
const activityEvents = [
    "mousemove",
    "mousedown",
    "keydown",
    "scroll",
    "touchstart"
];

activityEvents.forEach(event => {
    document.addEventListener(
        event,
        resetInactivityTimer,
        { passive: true }
    );
});

// Start timer when page loads
resetInactivityTimer();