package main

import (
	"fmt"
	"net/http"
	"runtime"
	"strconv"
	"syscall"
)

var held [][]byte

func main() {
	http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "ok\n")
	})

	http.HandleFunc("/eat", func(w http.ResponseWriter, r *http.Request) {
		mb, err := strconv.Atoi(r.URL.Query().Get("mb"))
		if err != nil || mb < 0 {
			http.Error(w, "invalid mb", http.StatusBadRequest)
			return
		}

		// Allocate and retain the memory so it cannot be garbage-collected.
		buf := make([]byte, mb*1024*1024)

		// Touch each page so the memory is actually committed.
		for i := 0; i < len(buf); i += 4096 {
			buf[i] = 1
		}

		held = append(held, buf)

		fmt.Fprintf(w, "allocated %d MB\n", mb)
	})

	http.HandleFunc("/hostname", func(w http.ResponseWriter, r *http.Request) {
		var u syscall.Utsname
	
		if err := syscall.Uname(&u); err != nil {
			http.Error(
				w,
				fmt.Sprintf("uname failed: %v\n", err),
				http.StatusInternalServerError,
			)
			return
		}
	
		var hostname []byte
		for _, c := range u.Nodename {
			if c == 0 {
				break
			}
			hostname = append(hostname, byte(c))
		}
	
		fmt.Fprintf(w, "%s\n", hostname)
	})

	http.HandleFunc("/burn", func(w http.ResponseWriter, r *http.Request) {
		// Keep this goroutine pinned to one OS thread and spin forever.
		runtime.LockOSThread()
		for {
		}
	})

	fmt.Println("api listening on :8080")
	if err := http.ListenAndServe(":8080", nil); err != nil {
		panic(err)
	}
}