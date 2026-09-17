package main

import (
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
)

const mebibyte = 1024 * 1024

var (
	allocationsMu sync.Mutex
	allocations   [][]byte
	burnCounter   atomic.Uint64
)

func main() {
	addr := ":8080"
	if port := os.Getenv("PORT"); port != "" {
		addr = ":" + port
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", healthHandler)
	mux.HandleFunc("GET /eat", eatHandler)
	mux.HandleFunc("GET /burn", burnHandler)

	log.Printf("api listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, mux))
}

func healthHandler(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	fmt.Fprintln(w, "ok")
}

func eatHandler(w http.ResponseWriter, r *http.Request) {
	mb, err := strconv.ParseUint(r.URL.Query().Get("mb"), 10, 64)
	maxInt := uint64(^uint(0) >> 1)
	if err != nil || mb == 0 || mb > maxInt/mebibyte {
		http.Error(w, "mb must be a positive integer", http.StatusBadRequest)
		return
	}

	block := make([]byte, int(mb*mebibyte))
	// Touch every page so Linux actually backs the virtual allocation with memory.
	for i := 0; i < len(block); i += 4096 {
		block[i] = 1
	}
	block[len(block)-1] = 1

	allocationsMu.Lock()
	allocations = append(allocations, block)
	totalBlocks := len(allocations)
	allocationsMu.Unlock()

	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	fmt.Fprintf(w, "allocated and holding %d MiB (%d allocation(s))\n", mb, totalBlocks)
}

func burnHandler(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(http.StatusAccepted)
	_, _ = fmt.Fprintln(w, "burning one CPU core")
	if flusher, ok := w.(http.Flusher); ok {
		flusher.Flush()
	}

	for {
		burnCounter.Add(1)
	}
}
