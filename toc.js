// Populate the sidebar
//
// This is a script, and not included directly in the page, to control the total size of the book.
// The TOC contains an entry for each page, so if each page includes a copy of the TOC,
// the total size of the page becomes O(n**2).
class MDBookSidebarScrollbox extends HTMLElement {
    constructor() {
        super();
    }
    connectedCallback() {
        this.innerHTML = '<ol class="chapter"><li class="chapter-item expanded affix "><a href="vision.html">Introduction</a></li><li class="chapter-item expanded "><a href="getting-started.html"><strong aria-hidden="true">1.</strong> Getting started</a></li><li class="chapter-item expanded "><a href="agents.html"><strong aria-hidden="true">2.</strong> Using it with agents</a></li><li class="chapter-item expanded "><a href="reference.html"><strong aria-hidden="true">3.</strong> Reference</a></li><li class="chapter-item expanded "><a href="governance.html"><strong aria-hidden="true">4.</strong> Governance resources</a></li><li class="chapter-item expanded "><a href="releases.html"><strong aria-hidden="true">5.</strong> Source releases</a></li><li class="chapter-item expanded "><a href="why-camayoc.html"><strong aria-hidden="true">6.</strong> Why camayoc</a></li><li class="chapter-item expanded "><a href="the-stack.html"><strong aria-hidden="true">7.</strong> The stack</a></li><li class="chapter-item expanded "><a href="metrics.html"><strong aria-hidden="true">8.</strong> Metrics</a></li><li class="chapter-item expanded affix "><li class="part-title">How it works: design notes</li><li class="chapter-item expanded "><a href="design/ingress.html"><strong aria-hidden="true">9.</strong> Ingress: how knowledge earns its way in</a></li><li class="chapter-item expanded "><a href="design/bootstrap-ontology.html"><strong aria-hidden="true">10.</strong> The bootstrap ontology</a></li><li class="chapter-item expanded "><a href="design/what-belongs-in-the-graph.html"><strong aria-hidden="true">11.</strong> What belongs in the graph</a></li><li class="chapter-item expanded "><a href="design/skill.html"><strong aria-hidden="true">12.</strong> The skill is the interface</a></li><li class="chapter-item expanded "><a href="design/task-lifecycle-slice.html"><strong aria-hidden="true">13.</strong> First slice: task lifecycle and decisions</a></li><li class="chapter-item expanded "><a href="design/workflow-and-archive.html"><strong aria-hidden="true">14.</strong> Workflow runs and the archive</a></li><li class="chapter-item expanded "><a href="design/golden-paths.html"><strong aria-hidden="true">15.</strong> Golden paths</a></li><li class="chapter-item expanded "><a href="design/certified-knowledge-packs.html"><strong aria-hidden="true">16.</strong> Certified knowledge packs</a></li><li class="chapter-item expanded "><a href="design/entity-mentions.html"><strong aria-hidden="true">17.</strong> Prose entity mentions</a></li><li class="chapter-item expanded "><a href="design/rml-executor.html"><strong aria-hidden="true">18.</strong> Governed RML-subset execution</a></li><li class="chapter-item expanded "><a href="design/work-cost.html"><strong aria-hidden="true">19.</strong> Request cost attribution</a></li><li class="chapter-item expanded "><a href="design/jev-typed-decisions.html"><strong aria-hidden="true">20.</strong> Jev: typed decisions</a></li><li class="chapter-item expanded "><a href="design/thesis-boundary.html"><strong aria-hidden="true">21.</strong> The camayoc / yupana thesis boundary</a></li><li class="chapter-item expanded "><a href="design/paper.html"><strong aria-hidden="true">22.</strong> Paper plan</a></li><li class="chapter-item expanded "><a href="design/implemented-set.html"><strong aria-hidden="true">23.</strong> The implemented set (historical)</a></li><li class="chapter-item expanded "><a href="design/incident-corpus.html"><strong aria-hidden="true">24.</strong> The 2026-08-06/07 incident corpus (historical)</a></li><li class="chapter-item expanded affix "><li class="part-title">Map</li><li class="chapter-item expanded "><a href="docs-map.html"><strong aria-hidden="true">25.</strong> Docs map</a></li></ol>';
        // Set the current, active page, and reveal it if it's hidden
        let current_page = document.location.href.toString().split("#")[0].split("?")[0];
        if (current_page.endsWith("/")) {
            current_page += "index.html";
        }
        var links = Array.prototype.slice.call(this.querySelectorAll("a"));
        var l = links.length;
        for (var i = 0; i < l; ++i) {
            var link = links[i];
            var href = link.getAttribute("href");
            if (href && !href.startsWith("#") && !/^(?:[a-z+]+:)?\/\//.test(href)) {
                link.href = path_to_root + href;
            }
            // The "index" page is supposed to alias the first chapter in the book.
            if (link.href === current_page || (i === 0 && path_to_root === "" && current_page.endsWith("/index.html"))) {
                link.classList.add("active");
                var parent = link.parentElement;
                if (parent && parent.classList.contains("chapter-item")) {
                    parent.classList.add("expanded");
                }
                while (parent) {
                    if (parent.tagName === "LI" && parent.previousElementSibling) {
                        if (parent.previousElementSibling.classList.contains("chapter-item")) {
                            parent.previousElementSibling.classList.add("expanded");
                        }
                    }
                    parent = parent.parentElement;
                }
            }
        }
        // Track and set sidebar scroll position
        this.addEventListener('click', function(e) {
            if (e.target.tagName === 'A') {
                sessionStorage.setItem('sidebar-scroll', this.scrollTop);
            }
        }, { passive: true });
        var sidebarScrollTop = sessionStorage.getItem('sidebar-scroll');
        sessionStorage.removeItem('sidebar-scroll');
        if (sidebarScrollTop) {
            // preserve sidebar scroll position when navigating via links within sidebar
            this.scrollTop = sidebarScrollTop;
        } else {
            // scroll sidebar to current active section when navigating via "next/previous chapter" buttons
            var activeSection = document.querySelector('#sidebar .active');
            if (activeSection) {
                activeSection.scrollIntoView({ block: 'center' });
            }
        }
        // Toggle buttons
        var sidebarAnchorToggles = document.querySelectorAll('#sidebar a.toggle');
        function toggleSection(ev) {
            ev.currentTarget.parentElement.classList.toggle('expanded');
        }
        Array.from(sidebarAnchorToggles).forEach(function (el) {
            el.addEventListener('click', toggleSection);
        });
    }
}
window.customElements.define("mdbook-sidebar-scrollbox", MDBookSidebarScrollbox);
