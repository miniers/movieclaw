package main

import (
	"errors"

	"github.com/movieclaw/movieclaw/cli/internal/clierr"
)

func asCli(err error, target **clierr.Error) bool {
	return errors.As(err, target)
}
