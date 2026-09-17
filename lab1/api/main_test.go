package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHealthHandler(t *testing.T) {
	recorder := httptest.NewRecorder()
	healthHandler(recorder, httptest.NewRequest(http.MethodGet, "/health", nil))

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusOK)
	}
	if recorder.Body.String() != "ok\n" {
		t.Fatalf("body = %q, want %q", recorder.Body.String(), "ok\n")
	}
}

func TestEatHandlerRejectsInvalidInput(t *testing.T) {
	for _, target := range []string{"/eat", "/eat?mb=0", "/eat?mb=nope"} {
		t.Run(target, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			eatHandler(recorder, httptest.NewRequest(http.MethodGet, target, nil))

			if recorder.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want %d", recorder.Code, http.StatusBadRequest)
			}
		})
	}
}

func TestEatHandlerKeepsAllocation(t *testing.T) {
	allocationsMu.Lock()
	allocations = nil
	allocationsMu.Unlock()
	t.Cleanup(func() {
		allocationsMu.Lock()
		allocations = nil
		allocationsMu.Unlock()
	})

	recorder := httptest.NewRecorder()
	eatHandler(recorder, httptest.NewRequest(http.MethodGet, "/eat?mb=1", nil))

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusOK)
	}
	if !strings.Contains(recorder.Body.String(), "1 MiB") {
		t.Fatalf("body = %q, want allocation size", recorder.Body.String())
	}

	allocationsMu.Lock()
	defer allocationsMu.Unlock()
	if len(allocations) != 1 || len(allocations[0]) != mebibyte {
		t.Fatalf("held allocations = %d, want one 1 MiB allocation", len(allocations))
	}
}
