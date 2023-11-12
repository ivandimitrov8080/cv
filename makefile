main = $(pname).tsx

default: all
all:
	-bun $(main)

clean:
	rm -f $(pname)

install: $(pname)
	mkdir -p $(out)/bin
	install $(pname).pdf $(out)/bin/$(pname)

