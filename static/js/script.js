// CypherLoom Main JavaScript

document.addEventListener('DOMContentLoaded', function() {
    // Animated C Character
    animateCChar();
    
    // Mobile Navigation Toggle
    initMobileNav();
    
    // Animated Grid Cards
    initAnimatedGrid();
    
    // Search with Debouncing
    initSearch();
    
    // Dynamic Year Selection
    initYearSelector();
    
    // Smooth Scrolling
    initSmoothScroll();
    
    // Form Validation
    initFormValidation();
    
    // Loading States
    initLoadingStates();
    
    // Particle Background
    initParticles();
});

function animateCChar() {
    const cChars = document.querySelectorAll('.c-char');
    cChars.forEach((char, index) => {
        char.style.animationDelay = `${index * 0.1}s`;
    });
}

function initMobileNav() {
    const toggler = document.querySelector('.navbar-toggler');
    const nav = document.querySelector('.navbar-collapse');
    
    if (toggler && nav) {
        toggler.addEventListener('click', function() {
            this.setAttribute('aria-expanded', 
                this.getAttribute('aria-expanded') === 'true' ? 'false' : 'true'
            );
        });
        
        // Close mobile nav when clicking outside
        document.addEventListener('click', function(event) {
            if (!nav.contains(event.target) && !toggler.contains(event.target)) {
                toggler.setAttribute('aria-expanded', 'false');
                nav.classList.remove('show');
            }
        });
    }
}

function initAnimatedGrid() {
    const observerOptions = {
        root: null,
        rootMargin: '0px',
        threshold: 0.1
    };

    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry, index) => {
            if (entry.isIntersecting) {
                setTimeout(() => {
                    entry.target.classList.add('animated');
                }, index * 100);
            }
        });
    }, observerOptions);

    document.querySelectorAll('.grid-card').forEach(card => {
        observer.observe(card);
    });
}

function initSearch() {
    const searchInput = document.querySelector('.search-input');
    const searchResults = document.querySelector('.search-results');
    
    if (!searchInput) return;
    
    let timeout;
    searchInput.addEventListener('input', function() {
        clearTimeout(timeout);
        
        if (this.value.length < 2) {
            if (searchResults) searchResults.innerHTML = '';
            return;
        }
        
        timeout = setTimeout(() => {
            fetchSearchSuggestions(this.value);
        }, 300);
    });
}

function fetchSearchSuggestions(query) {
    fetch(`/api/search_suggestions?q=${encodeURIComponent(query)}`)
        .then(response => response.json())
        .then(data => {
            displaySearchSuggestions(data);
        })
        .catch(error => {
            console.error('Search error:', error);
        });
}

function displaySearchSuggestions(suggestions) {
    const container = document.querySelector('.search-results');
    if (!container) return;
    
    if (suggestions.length === 0) {
        container.innerHTML = '<div class="no-results">No results found</div>';
        return;
    }
    
    container.innerHTML = suggestions.map(item => `
        <div class="search-result-item" onclick="window.location.href='/resource/${item.id}'">
            <strong>${item.title}</strong>
            <small class="text-muted">${item.subject} • ${item.type}</small>
        </div>
    `).join('');
}

function initYearSelector() {
    const resourceTypeSelect = document.querySelector('select[name="resource_type"]');
    const yearWrapper = document.querySelector('.year-select-wrapper');
    
    if (resourceTypeSelect && yearWrapper) {
        resourceTypeSelect.addEventListener('change', function() {
            if (this.value === 'pyq') {
                yearWrapper.style.display = 'block';
                yearWrapper.classList.add('fade-in-up');
            } else {
                yearWrapper.style.display = 'none';
            }
        });
    }
}

function initSmoothScroll() {
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function(e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                target.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });
}

function initFormValidation() {
    const forms = document.querySelectorAll('.needs-validation');
    
    forms.forEach(form => {
        form.addEventListener('submit', function(event) {
            if (!form.checkValidity()) {
                event.preventDefault();
                event.stopPropagation();
            }
            form.classList.add('was-validated');
        });
    });
}

function initLoadingStates() {
    document.querySelectorAll('form').forEach(form => {
        form.addEventListener('submit', function() {
            const submitBtn = this.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.innerHTML = '<div class="loading-spinner"></div>';
                submitBtn.disabled = true;
            }
        });
    });
}

function initParticles() {
    const container = document.querySelector('.hero-section');
    if (!container) return;
    
    for (let i = 0; i < 20; i++) {
        const particle = document.createElement('div');
        particle.className = 'particle';
        
        const size = Math.random() * 20 + 5;
        particle.style.width = `${size}px`;
        particle.style.height = `${size}px`;
        
        particle.style.left = `${Math.random() * 100}%`;
        particle.style.top = `${Math.random() * 100}%`;
        
        particle.style.animationDelay = `${Math.random() * 20}s`;
        particle.style.animationDuration = `${Math.random() * 10 + 10}s`;
        
        container.appendChild(particle);
    }
}

// Real-time Search for Resources
function performSearch() {
    const searchForm = document.getElementById('resourceSearchForm');
    if (searchForm) {
        const inputs = searchForm.querySelectorAll('input, select');
        
        inputs.forEach(input => {
            input.addEventListener('change', function() {
                if (this.type !== 'text' || this.value.length >= 2 || this.value.length === 0) {
                    searchForm.submit();
                }
            });
            
            if (input.type === 'text') {
                input.addEventListener('input', debounce(function() {
                    if (this.value.length >= 2 || this.value.length === 0) {
                        searchForm.submit();
                    }
                }, 500));
            }
        });
    }
}

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// File Upload Preview
function previewFile(input) {
    if (input.files && input.files[0]) {
        const reader = new FileReader();
        const preview = document.getElementById('filePreview');
        
        reader.onload = function(e) {
            preview.innerHTML = `
                <div class="file-preview">
                    <i class="fas fa-file fa-3x"></i>
                    <div>
                        <strong>${input.files[0].name}</strong>
                        <small>${(input.files[0].size / 1024).toFixed(1)} KB</small>
                    </div>
                </div>
            `;
        };
        
        reader.readAsDataURL(input.files[0]);
    }
}

// Progress Tracking
function updateProgress(progressId) {
    fetch(`/update_progress/${progressId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => {
        if (response.ok) {
            location.reload();
        }
    });
}

// Copy to Clipboard
function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showToast('Copied to clipboard!', 'success');
    }).catch(err => {
        showToast('Failed to copy', 'error');
    });
}

// Toast Notification
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
        <div class="toast-content">
            <i class="fas fa-${type === 'success' ? 'check-circle' : 'info-circle'}"></i>
            <span>${message}</span>
        </div>
    `;
    
    document.body.appendChild(toast);
    
    setTimeout(() => toast.classList.add('show'), 100);
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// Initialize everything when DOM is loaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
} else {
    initAll();
}

function initAll() {
    performSearch();
}