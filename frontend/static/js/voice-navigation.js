
let voiceEnabled = false;

function speak(text) {
    if (!voiceEnabled || !text.trim()) return;

    window.speechSynthesis.cancel();

    let message = new SpeechSynthesisUtterance(text);
    message.rate = 0.9;
    message.lang = "en-US";

    window.speechSynthesis.speak(message);
}

document.addEventListener("mouseover", function(event) {
    let element = event.target.closest(
        "button, a, input, select, textarea, label"
    );

    if (!element) return;

    let text = element.getAttribute("aria-label") ||
               element.innerText ||
               element.placeholder ||
               element.value ||
               "";

    speak(text);
});

document.addEventListener("focusin", function(event) {
    let element = event.target.closest(
        "button, a, input, select, textarea"
    );

    if (!element) return;

    let text = element.getAttribute("aria-label") ||
               element.innerText ||
               element.placeholder ||
               element.value ||
               "";

    speak(text);
});

function toggleVoice() {
    voiceEnabled = !voiceEnabled;

    window.speechSynthesis.cancel();

    document.getElementById("voiceToggle").textContent =
        voiceEnabled ? "🔊 Voice On" : "🔇 Voice Off";

    if (voiceEnabled) {
        speak("Voice navigation enabled");
    }
}
