main = $(pname).tsx

default: dev
all:
	bun $(main)

clean:
	rm -f $(pname)

install: $(pname)
	mkdir -p $(out)
	install $(pname).pdf $(out)/$(pname)

dev:
	bun $(main)
	pkill -HUP mupdf

