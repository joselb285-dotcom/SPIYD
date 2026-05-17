/* ============================================================
   SPIYD — main.js   IIFE pattern — no ES modules
   v20260516
   ============================================================ */
(function () {
  "use strict";

  /* ---- utility ---- */
  function safe(fn, name) {
    try { fn(); } catch (e) { console.warn("[SPIYD:" + name + "]", e); }
  }

  /* ============================================================
     SPLASH
     ============================================================ */
  function initSplash() {
    var splash = document.querySelector("[data-splash]");
    if (!splash) return;
    function hide() { splash.classList.add("is-out"); }
    if (document.readyState === "complete") {
      setTimeout(hide, 900);
    } else {
      window.addEventListener("load", function () { setTimeout(hide, 700); });
    }
    setTimeout(hide, 4200); // safety
  }

  /* ============================================================
     NAV — solidify + hamburger
     ============================================================ */
  function initNav() {
    var nav = document.getElementById("nav");
    if (!nav) return;

    /* solidify on scroll */
    var sentinel = document.createElement("div");
    sentinel.style.cssText = "position:absolute;top:2px;height:1px;width:1px;pointer-events:none;";
    document.body.prepend(sentinel);
    var io = new IntersectionObserver(function (entries) {
      nav.classList.toggle("is-solid", !entries[0].isIntersecting);
    }, { threshold: 0 });
    io.observe(sentinel);

    /* hamburger */
    var burger = nav.querySelector(".nav-hamburger");
    var menu   = nav.querySelector(".nav-mobile");
    if (!burger || !menu) return;
    burger.addEventListener("click", function () {
      var open = burger.getAttribute("aria-expanded") === "true";
      burger.setAttribute("aria-expanded", String(!open));
      menu.classList.toggle("is-open", !open);
    });
    menu.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function () {
        burger.setAttribute("aria-expanded", "false");
        menu.classList.remove("is-open");
      });
    });
  }

  /* ============================================================
     MOUSE-REACTIVE GRADIENT
     ============================================================ */
  function initMouseGradient() {
    var el = document.querySelector("[data-mouse-gradient]");
    if (!el) return;
    if (window.matchMedia("(hover: none)").matches) return;

    var tx = 38, ty = 45, cx = 38, cy = 45;

    document.addEventListener("mousemove", function (e) {
      tx = (e.clientX / window.innerWidth)  * 100;
      ty = (e.clientY / window.innerHeight) * 100;
    });

    (function tick() {
      cx += (tx - cx) * 0.055;
      cy += (ty - cy) * 0.055;
      document.documentElement.style.setProperty("--mx", cx.toFixed(2) + "%");
      document.documentElement.style.setProperty("--my", cy.toFixed(2) + "%");
      requestAnimationFrame(tick);
    }());
  }

  /* ============================================================
     REVEAL ON SCROLL
     ============================================================ */
  function initReveals() {
    var els = document.querySelectorAll(".reveal");
    if (!els.length) return;

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add("is-visible");
        io.unobserve(e.target);
      });
    }, { threshold: 0.04, rootMargin: "0px 0px -4% 0px" });

    els.forEach(function (el) { io.observe(el); });

    /* safety net — force-reveal anything still hidden at 6s */
    setTimeout(function () {
      document.querySelectorAll(".reveal:not(.is-visible)").forEach(function (el) {
        if (el.getBoundingClientRect().top < window.innerHeight * 1.1) {
          el.classList.add("is-visible");
        }
      });
    }, 6000);
  }

  /* ============================================================
     COUNT-UP
     ============================================================ */
  function initCounters() {
    var els = document.querySelectorAll("[data-count-to]");
    if (!els.length) return;

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        io.unobserve(e.target);
        runCount(e.target);
      });
    }, { threshold: 0.6 });

    els.forEach(function (el) { io.observe(el); });

    function runCount(el) {
      var target   = parseInt(el.getAttribute("data-count-to"), 10);
      var suffix   = el.getAttribute("data-suffix") || "";
      var duration = 1900;
      var start    = performance.now();

      function tick(now) {
        var p  = Math.min((now - start) / duration, 1);
        var ep = 1 - Math.pow(1 - p, 3); /* ease-out cubic */
        var v  = Math.round(ep * target);
        if (target >= 1000000) {
          el.textContent = (v / 1000000).toFixed(1).replace(".", ",") + "M " + suffix;
        } else if (target >= 1000) {
          el.textContent = v.toLocaleString("es-AR") + (suffix ? " " + suffix : "");
        } else {
          el.textContent = v + (suffix ? " " + suffix : "");
        }
        if (p < 1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    }
  }

  /* ============================================================
     SMOOTH SCROLL FOR ANCHORS
     ============================================================ */
  function initSmoothScroll() {
    document.addEventListener("click", function (e) {
      var a = e.target.closest('a[href^="#"]');
      if (!a) return;
      var id = a.getAttribute("href");
      if (!id || id === "#") return;
      var target = document.querySelector(id);
      if (!target) return;
      e.preventDefault();
      window.scrollTo({
        top: target.getBoundingClientRect().top + window.scrollY - 84,
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"
      });
    });
  }

  /* ============================================================
     TERMINAL ANIMATION
     ============================================================ */
  function initTerminal() {
    var terminal = document.querySelector(".ai-terminal");
    var output   = document.querySelector(".terminal-output");
    if (!terminal || !output) return;

    var lines = output.querySelectorAll("span");
    /* hide all lines initially */
    lines.forEach(function (l) { l.style.opacity = "0"; });

    /* animate when in viewport */
    var io = new IntersectionObserver(function (entries) {
      if (!entries[0].isIntersecting) return;
      io.unobserve(terminal);
      lines.forEach(function (line, i) {
        setTimeout(function () {
          line.style.transition = "opacity 0.28s ease";
          line.style.opacity    = "1";
        }, i * 100);
      });
      /* blinking cursor */
      var cur = document.createElement("span");
      cur.className   = "terminal-cursor";
      cur.textContent = "█";
      setTimeout(function () { output.appendChild(cur); }, lines.length * 100 + 300);
    }, { threshold: 0.35 });

    io.observe(terminal);
  }

  /* ============================================================
     CONTACT FORM (index.html quick form)
     ============================================================ */
  function initContactForm() {
    var form = document.getElementById("contact-form");
    if (!form) return;

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (!form.reportValidity()) return;

      var btn     = form.querySelector("[type=submit]");
      var success = form.querySelector(".form-success");

      form.classList.add("is-sending");
      btn.disabled = true;

      /* Placeholder async — replace with your actual endpoint */
      setTimeout(function () {
        form.classList.remove("is-sending");
        form.reset();
        btn.disabled = false;
        if (success) {
          success.classList.add("is-visible");
          setTimeout(function () { success.classList.remove("is-visible"); }, 9000);
        }
      }, 1600);
    });
  }

  /* ============================================================
     SUBPAGE FORMS (demo, contacto, reunion)
     ============================================================ */
  function initSubpageForms() {
    var forms = document.querySelectorAll("[data-subpage-form]");
    forms.forEach(function (form) {
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        if (!form.reportValidity()) return;
        var btn     = form.querySelector("[type=submit]");
        var success = form.querySelector(".form-success");
        form.classList.add("is-sending");
        btn.disabled = true;
        setTimeout(function () {
          form.classList.remove("is-sending");
          form.reset();
          btn.disabled = false;
          if (success) {
            success.classList.add("is-visible");
            success.scrollIntoView({ behavior: "smooth", block: "nearest" });
          }
        }, 1600);
      });
    });
  }

  /* ============================================================
     LOGIN BUTTONS — redirect to system (configure URL below)
     ============================================================ */
  function initLoginButtons() {
    var SYSTEM_URL = "#"; /* ← Replace with actual system URL */
    var ids = ["btn-login", "btn-login-m", "hero-login", "footer-login"];
    ids.forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.addEventListener("click", function (e) {
        e.preventDefault();
        if (SYSTEM_URL === "#") {
          /* Development placeholder */
          alert("Ingresar al sistema SPIYD.\n\nConfigure la URL del sistema en main.js → SYSTEM_URL");
        } else {
          window.location.href = SYSTEM_URL;
        }
      });
    });
  }

  /* ============================================================
     GSAP SCROLLTRIGGER — optional enhancements
     ============================================================ */
  function initScrollEffects() {
    if (!window.gsap || !window.ScrollTrigger) return;
    gsap.registerPlugin(ScrollTrigger);

    /* Parallax on satellite section */
    var satImg = document.querySelector(".satellite-img");
    if (satImg) {
      gsap.fromTo(satImg,
        { y: "-8%" },
        {
          y: "8%",
          ease: "none",
          scrollTrigger: {
            trigger: ".satellite-section",
            start: "top bottom",
            end: "bottom top",
            scrub: 1
          }
        }
      );
    }

    /* Hero image subtle zoom */
    var heroBg = document.querySelector(".hero-bg-img");
    if (heroBg) {
      gsap.fromTo(heroBg,
        { scale: 1 },
        {
          scale: 1.06,
          ease: "none",
          scrollTrigger: {
            trigger: ".hero",
            start: "top top",
            end: "bottom top",
            scrub: 1.5
          }
        }
      );
    }

    /* Problem image reveal */
    var probImg = document.querySelector(".problem-image img");
    if (probImg) {
      gsap.fromTo(probImg,
        { scale: 1.08 },
        {
          scale: 1,
          ease: "none",
          scrollTrigger: {
            trigger: ".problem-image",
            start: "top bottom",
            end: "center center",
            scrub: 1.2
          }
        }
      );
    }
  }

  /* ============================================================
     BOOT
     ============================================================ */
  function boot() {
    safe(initSplash,        "splash");
    safe(initNav,           "nav");
    safe(initMouseGradient, "gradient");
    safe(initReveals,       "reveals");
    safe(initCounters,      "counters");
    safe(initSmoothScroll,  "scroll");
    safe(initTerminal,      "terminal");
    safe(initContactForm,   "form");
    safe(initSubpageForms,  "subforms");
    safe(initLoginButtons,  "login");
    safe(initScrollEffects, "gsap");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

}());
